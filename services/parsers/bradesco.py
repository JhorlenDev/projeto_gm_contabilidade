"""
Parser para extrato Bradesco Net Empresa (PDF).
Formato de colunas: Data | Lançamento | Dcto. | Crédito (R$) | Débito (R$) | Saldo (R$)
"""
from __future__ import annotations

import io
import re
from datetime import date
from decimal import Decimal

from .base import (
    ExtratoHeader,
    ExtratoResult,
    LancamentoExtrato,
    _parse_brl_decimal,
    _parse_date_br,
    _read_file_bytes,
)


class BradescoExtratoParser:
    """
    Parser especializado para extrato Bradesco Net Empresa (PDF).
    Formato de colunas: Data | Lançamento | Dcto. | Crédito (R$) | Débito (R$) | Saldo (R$)
    A natureza é determinada por qual coluna (Crédito ou Débito) contém o valor.
    """

    BANCOS_SUPORTADOS = ["bradesco"]

    # Linha de cabeçalho do extrato — usado para detectar o início dos lançamentos
    _HEADER_RE = re.compile(r"Data\s+Lan[çc]amento\s+Dcto", re.IGNORECASE)

    # Linha com data no início: DD/MM/YYYY
    _DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+(.+)$")

    # Valor BRL: números com pontos/vírgulas ex: 1.234,56 ou 25.151,62
    _VALUE_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})")

    def __init__(self):
        self._text: str = ""
        self._pages: list[str] = []
        self._warnings: list[str] = []
        self._errors: list[str] = []

    def parse(self, uploaded_file) -> ExtratoResult:
        try:
            from pypdf import PdfReader
        except ImportError:
            return ExtratoResult(
                success=False,
                erros=["Biblioteca pypdf não instalada. Execute: pip install pypdf"]
            )

        try:
            reader = PdfReader(io.BytesIO(_read_file_bytes(uploaded_file)))
        except Exception as exc:
            return ExtratoResult(success=False, erros=[f"Erro ao ler PDF: {exc}"])

        self._pages = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            self._pages.append(page_text)

        self._text = "\n".join(self._pages)

        if not self._text.strip():
            return ExtratoResult(success=False, erros=["Nenhum texto encontrado no PDF."])

        header = self._extract_header()
        lancamentos = self._extract_lancamentos()

        return ExtratoResult(
            success=True,
            header=header,
            lancamentos=lancamentos,
            total_lancamentos=len(lancamentos),
            avisos=self._warnings,
            erros=self._errors,
        )

    def _extract_header(self) -> ExtratoHeader:
        header = ExtratoHeader()
        full_text = self._text

        # Empresa: linha com "| CNPJ:"
        m = re.search(r"([A-Z][^\n|]+?)\s*\|\s*CNPJ[:\s]*([\d./-]+)", full_text)
        if m:
            header.empresa_nome = m.group(1).strip()
            header.empresa_cnpj = m.group(2).strip()
        else:
            # Fallback: CNPJ isolado
            m2 = re.search(r"CNPJ[:\s]*([\d]{2}[\.\d]{11}[\/]?\d{4}[-]?\d{2})", full_text, re.IGNORECASE)
            if m2:
                header.empresa_cnpj = m2.group(1)

        # Agência e conta
        m = re.search(r"AG[:\s]*(\d+)\s*\|\s*(?:CC|Conta)[:\s]*([\d-]+)", full_text, re.IGNORECASE)
        if m:
            header.agencia = m.group(1)
            header.conta = m.group(2)

        # Período
        m = re.search(r"Entre\s+(\d{2}/\d{2}/\d{4})\s+e\s+(\d{2}/\d{2}/\d{4})", full_text, re.IGNORECASE)
        if m:
            header.periodo_inicio = _parse_date_br(m.group(1))
            header.periodo_fim = _parse_date_br(m.group(2))

        # Saldo disponível
        m = re.search(r"Total\s+Dispon[íi]vel\s*\(R\$\)\s*([\d.,]+)", full_text, re.IGNORECASE)
        if m:
            header.saldo = _parse_brl_decimal(m.group(1))

        header.dados_brutos = {"banco": "bradesco", "paginas": len(self._pages)}
        return header

    def _extract_lancamentos(self) -> list[LancamentoExtrato]:
        """
        Estratégia: percorre todas as linhas e agrupa blocos por data.
        Cada bloco de data pode ter múltiplas linhas de lançamento.
        """
        lancamentos: list[LancamentoExtrato] = []
        lines = [line.strip() for line in self._text.splitlines()]

        _SKIP_RE = re.compile(
            r"^(Folha|Extrato\s+Mensal|A\s+MESQUITA|Nome\s+do|Data\s+da|Data\s+Lan[çc]|Ag[êe]ncia\s*\|"
            r"|Agência\s*\|\s*Conta|Total\s+Dispon|Últimos\s+Lançamentos|SALDO\s+ANTERIOR|Os\s+dados\s+acima"
            r"|Não\s+há\s+lan|Saldos\s+Invest|^\s*$)", re.IGNORECASE
        )

        _SUMMARY_RE = re.compile(r"^Total\b", re.IGNORECASE)

        blocks: list[tuple[str, list[str]]] = []
        current_date: str | None = None
        current_block: list[str] = []

        for line in lines:
            if _SKIP_RE.search(line):
                continue

            m = self._DATE_RE.match(line)
            if m:
                rest = m.group(2).strip()
                if _SUMMARY_RE.match(rest):
                    continue
                if current_date and current_block:
                    blocks.append((current_date, current_block))
                current_date = m.group(1)
                current_block = [rest] if rest else []
            elif current_date is not None:
                current_block.append(line)

        if current_date and current_block:
            blocks.append((current_date, current_block))

        prev_saldo: Decimal | None = None
        line_idx = 0

        for date_str, block_lines in blocks:
            data = _parse_date_br(date_str)
            if not data:
                continue

            sub_lancamentos = self._split_block_into_lancamentos(block_lines)

            for sub in sub_lancamentos:
                line_idx += 1
                descricao = sub["descricao"]
                documento = sub["documento"]
                valor = sub["valor"]
                saldo = sub["saldo"]

                if valor <= 0:
                    continue

                natureza = sub.get("natureza", "")
                if not natureza and saldo is not None and prev_saldo is not None:
                    diff = saldo - prev_saldo
                    if diff > 0:
                        natureza = "CREDITO"
                    else:
                        natureza = "DEBITO"
                elif not natureza:
                    desc_upper = descricao.upper()
                    credito_kw = ("DEPOSITO", "DEP ", "RECEBI", "CREDITO", "CRÉDITO", "VENDA CART",
                                  "CIELO", "PIX QR CODE", "TRANSFERENCIA PIX\nREM:", "REM:")
                    debito_kw = ("PAGTO", "PAGAMENTO", "TRANSF CC", "TARIFA", "GASTOS CART",
                                 "OPERACAO CAPITAL", "DEBITO", "DÉBITO", "DES:")
                    if any(kw in desc_upper for kw in credito_kw):
                        natureza = "CREDITO"
                    elif any(kw in desc_upper for kw in debito_kw):
                        natureza = "DEBITO"

                if saldo is not None:
                    prev_saldo = saldo

                lancamentos.append(LancamentoExtrato(
                    linha_origem=line_idx,
                    pagina=1,
                    data=data,
                    descricao_original=descricao,
                    documento=documento,
                    valor=valor,
                    natureza_inferida=natureza,
                    saldo=saldo,
                    linha_original=" | ".join(block_lines[:3]),
                ))

        return lancamentos

    def _split_block_into_lancamentos(self, block_lines: list[str]) -> list[dict]:
        """
        Divide as linhas de um bloco (mesmo dia) em sub-lançamentos.
        """
        results = []
        pending_desc_lines: list[str] = []

        for line in block_lines:
            values = self._VALUE_RE.findall(line)
            if not values:
                pending_desc_lines.append(line)
                continue

            desc_part = self._VALUE_RE.sub("", line).strip()
            desc_part = re.sub(r"\s{2,}", " ", desc_part).strip()
            # Remove traço residual de valores negativos (ex: "-11.571,73" deixa "-" após remoção do número)
            desc_part = re.sub(r"\s*-\s*$", "", desc_part).strip()

            full_desc_lines = pending_desc_lines + ([desc_part] if desc_part else [])
            pending_desc_lines = []

            documento = ""
            full_desc = " ".join(full_desc_lines).strip()
            doc_match = re.search(r"\b(\d{4,10})\s*$", full_desc)
            if doc_match:
                documento = doc_match.group(1)
                full_desc = full_desc[:doc_match.start()].strip()

            decimals = [_parse_brl_decimal(v) for v in values]

            if len(decimals) == 1:
                saldo = decimals[0]
                valor = Decimal("0")
            elif len(decimals) >= 2:
                valor = decimals[-2]
                saldo = decimals[-1]
            else:
                continue

            natureza = ""
            raw_values_in_line = self._VALUE_RE.findall(line)
            if raw_values_in_line and len(raw_values_in_line) >= 2:
                penultimate_str = raw_values_in_line[-2]
                idx_in_line = line.rfind(penultimate_str)
                if idx_in_line > 0 and line[idx_in_line - 1] == "-":
                    natureza = "DEBITO"
                    valor = _parse_brl_decimal(penultimate_str)

            results.append({
                "descricao": full_desc,
                "documento": documento,
                "valor": abs(valor) if valor > 0 else Decimal("0"),
                "saldo": saldo,
                "natureza": natureza,
            })

        # Remove sub-lançamentos cujo descrição é linha de resumo/total (ex: "Total -")
        results = [r for r in results if not re.match(r"^Total\b", r["descricao"], re.IGNORECASE)]

        return results
