"""
Parser para extrato Bradesco Net Empresa (PDF).
Formato de colunas: Data | Lançamento | Dcto. | Crédito (R$) | Débito (R$) | Saldo (R$)
Utiliza PyPDF para extrair o texto (devido a incompatibilidade do pdfplumber com alguns PDFs do Bradesco) e Pandas para organizar os dados.
"""
from __future__ import annotations

import io
import re
from datetime import date
from decimal import Decimal
import pandas as pd

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
    """

    BANCOS_SUPORTADOS = ["bradesco"]
    _DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+(.+)$")
    _VALUE_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})")

    def parse(self, uploaded_file) -> ExtratoResult:
        try:
            from pypdf import PdfReader
        except ImportError:
            return ExtratoResult(
                success=False,
                erros=["Biblioteca pypdf não instalada."]
            )

        try:
            reader = PdfReader(io.BytesIO(_read_file_bytes(uploaded_file)))
        except Exception as exc:
            return ExtratoResult(success=False, erros=[f"Erro ao ler PDF: {exc}"])

        self._pages = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            self._pages.append(page_text)

        full_text = "\n".join(self._pages)

        if not full_text.strip():
            return ExtratoResult(success=False, erros=["Nenhum texto encontrado no PDF."])

        header = self._extract_header(full_text)
        dados_brutos = self._extract_raw_data(full_text)
        
        if not dados_brutos:
            return ExtratoResult(
                success=True,
                header=header,
                lancamentos=[],
                total_lancamentos=0,
            )

        # Usando o Pandas para organizar e higienizar
        df = pd.DataFrame(dados_brutos)
        
        # Higienização
        df['descricao'] = df['descricao'].fillna("").astype(str).str.strip()
        df['documento'] = df['documento'].fillna("").astype(str).str.strip()
        df['natureza'] = df['natureza'].fillna("").astype(str).str.strip()
        
        # Remove transações zeradas comuns, mantendo linhas informativas de saldo.
        df = df[
            (df['valor_decimal'] > Decimal("0"))
            | (df['natureza'].isin(["SALDO_ANTERIOR", "SALDO_FINAL"]))
        ]

        lancamentos = []
        for _, row in df.iterrows():
            lancamentos.append(LancamentoExtrato(
                data=row['data_obj'],
                descricao_original=row['descricao'],
                valor=row['valor_decimal'],
                natureza_inferida=row['natureza'],
                documento=row['documento'],
                saldo=row.get('saldo_decimal'),
            ))

        header.dados_brutos["saldo_anterior"] = str(
            next((l.saldo if l.saldo is not None else l.valor for l in lancamentos if l.natureza_inferida == "SALDO_ANTERIOR"), Decimal("0"))
        )
        header.saldo = next(
            (l.saldo if l.saldo is not None else l.valor for l in reversed(lancamentos) if l.natureza_inferida == "SALDO_FINAL"),
            header.saldo,
        )
        header.dados_brutos["total_debito"] = str(
            sum((l.valor for l in lancamentos if l.natureza_inferida == "DEBITO"), Decimal("0"))
        )
        header.dados_brutos["total_credito"] = str(
            sum((l.valor for l in lancamentos if l.natureza_inferida == "CREDITO"), Decimal("0"))
        )

        return ExtratoResult(
            success=True,
            header=header,
            lancamentos=lancamentos,
            total_lancamentos=len(lancamentos),
        )

    def _extract_header(self, full_text: str) -> ExtratoHeader:
        header = ExtratoHeader()
        
        m = re.search(r"([A-Z][^\n|]+?)\s*\|\s*CNPJ[:\s]*([\d./-]+)", full_text)
        if m:
            header.empresa_nome = m.group(1).strip()
            header.empresa_cnpj = m.group(2).strip()
        else:
            m2 = re.search(r"CNPJ[:\s]*([\d]{2}[\.\d]{11}[\/]?\d{4}[-]?\d{2})", full_text, re.IGNORECASE)
            if m2:
                header.empresa_cnpj = m2.group(1)

        account_patterns = [
            r"AG[:\s]*(\d+)\s*\|\s*(?:CC|Conta)[:\s]*([\d.\-]+)",
            r"Ag[êe]ncia\s*[:|]?\s*(\d+)\s*(?:\||/|-)?\s*(?:Conta|CC)\s*[:|]?\s*([\d.\-]+)",
            r"Ag[êe]ncia\s+(\d+)\s+Conta\s+([\d.\-]+)",
            r"Ag[êe]ncia\s*\|\s*Conta.*?\n\s*(\d+)\s*\|\s*([\d.\-]+)",
        ]
        for pattern in account_patterns:
            m = re.search(pattern, full_text, re.IGNORECASE | re.DOTALL)
            if m:
                header.agencia = m.group(1).strip()
                header.conta = m.group(2).strip()
                break

        m = re.search(r"Entre\s+(\d{2}/\d{2}/\d{4})\s+e\s+(\d{2}/\d{2}/\d{4})", full_text, re.IGNORECASE)
        if m:
            header.periodo_inicio = _parse_date_br(m.group(1))
            header.periodo_fim = _parse_date_br(m.group(2))

        header.dados_brutos = {"banco": "bradesco", "paginas": len(self._pages)}
        return header

    def _extract_raw_data(self, full_text: str) -> list[dict]:
        lines = [line.strip() for line in full_text.splitlines()]

        _SKIP_RE = re.compile(
            r"^(Folha|Extrato\s+Mensal|A\s+MESQUITA|Nome\s+do|Data\s+da|Data\s+Lan[çc]|Ag[êe]ncia\s*\|"
            r"|Agência\s*\|\s*Conta|Total\s+Dispon|Últimos\s+Lançamentos|SALDO\s+ANTERIOR|Os\s+dados\s+acima"
            r"|Não\s+há\s+lan|Saldos\s+Invest|^\s*$)", re.IGNORECASE
        )
        _SUMMARY_RE = re.compile(r"^Total\b", re.IGNORECASE)

        blocks: list[tuple[str, list[str], int]] = []
        dados = []
        current_date: str | None = None
        current_block: list[str] = []
        current_order = 0

        for line_number, line in enumerate(lines, start=1):
            saldo_anterior_match = re.match(r"^(\d{2}/\d{2}/\d{4})\s+SALDO\s+ANTERIOR\s+(-?\d{1,3}(?:\.\d{3})*,\d{2})$", line, re.IGNORECASE)
            if saldo_anterior_match:
                if current_date and current_block:
                    blocks.append((current_date, current_block, current_order))
                    current_block = []
                data_saldo = _parse_date_br(saldo_anterior_match.group(1))
                saldo = _parse_brl_decimal(saldo_anterior_match.group(2))
                if data_saldo:
                    dados.append({
                        "data_obj": data_saldo,
                        "descricao": "SALDO ANTERIOR",
                        "documento": "",
                        "valor_decimal": abs(saldo),
                        "saldo_decimal": saldo,
                        "natureza": "SALDO_ANTERIOR",
                        "ordem": line_number,
                    })
                current_date = None
                continue

            total_match = re.match(r"^Total\s+[-\d]", line, re.IGNORECASE)
            if total_match:
                if current_date and current_block:
                    blocks.append((current_date, current_block, current_order))
                    current_block = []
                values = self._VALUE_RE.findall(line)
                data_total = _parse_date_br(current_date) if current_date else None
                if data_total and values:
                    saldo = _parse_brl_decimal(values[-1])
                    dados.append({
                        "data_obj": data_total,
                        "descricao": "SALDO FINAL",
                        "documento": "",
                        "valor_decimal": abs(saldo),
                        "saldo_decimal": saldo,
                        "natureza": "SALDO_FINAL",
                        "ordem": line_number,
                    })
                current_date = None
                continue

            if _SKIP_RE.search(line):
                continue

            m = self._DATE_RE.match(line)
            if m:
                rest = m.group(2).strip()
                if _SUMMARY_RE.match(rest):
                    continue
                if current_date and current_block:
                    blocks.append((current_date, current_block, current_order))
                current_date = m.group(1)
                current_order = line_number
                current_block = [rest] if rest else []
            elif current_date is not None:
                current_block.append(line)

        if current_date and current_block:
            blocks.append((current_date, current_block, current_order))

        prev_saldo: Decimal | None = None

        for date_str, block_lines, block_order in blocks:
            data = _parse_date_br(date_str)
            if not data:
                continue

            sub_lancamentos = self._split_block_into_dicts(block_lines)

            for sub in sub_lancamentos:
                descricao = sub["descricao"]
                documento = sub["documento"]
                valor = sub["valor"]
                saldo = sub["saldo"]
                natureza = sub.get("natureza", "")

                if not natureza and saldo is not None and prev_saldo is not None:
                    diff = saldo - prev_saldo
                    natureza = "CREDITO" if diff > 0 else "DEBITO"
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
                    
                dados.append({
                    "data_obj": data,
                    "descricao": descricao,
                    "documento": documento,
                    "valor_decimal": valor,
                    "natureza": natureza,
                    "saldo_decimal": saldo,
                    "ordem": block_order,
                })

        dados.sort(key=lambda row: row.get("ordem", 0))
        return dados

    def _split_block_into_dicts(self, block_lines: list[str]) -> list[dict]:
        results = []
        pending_desc_lines: list[str] = []

        for line in block_lines:
            values = self._VALUE_RE.findall(line)
            if not values:
                pending_desc_lines.append(line)
                continue

            desc_part = self._VALUE_RE.sub("", line).strip()
            desc_part = re.sub(r"\s{2,}", " ", desc_part).strip()
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

            if not re.match(r"^Total\b", full_desc, re.IGNORECASE):
                results.append({
                    "descricao": full_desc,
                    "documento": documento,
                    "valor": abs(valor),
                    "saldo": saldo,
                    "natureza": natureza,
                })

        return results
