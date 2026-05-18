"""
Parser para extrato Santander Extrato Consolidado Inteligente PJ.
Utiliza pdfplumber e pandas para extrair e organizar os dados.
"""
from __future__ import annotations

import io
import calendar
import re
from datetime import date
from decimal import Decimal
import pandas as pd

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

from .base import (
    ExtratoHeader,
    ExtratoResult,
    LancamentoExtrato,
    _parse_brl_decimal,
    _read_file_bytes,
)


class SantanderExtratoParser:
    """
    Extrato Consolidado Inteligente do Santander Empresas.
    O PDF tem colunas: Data | Descrição | Nº Documento | Créditos | Débitos | Saldo
    """

    _DATE_RE = re.compile(r"^(\d{2}/\d{2})\s+(.+)$")
    _VALUE_RE = re.compile(r"([\d.]+,\d{2})(-?)")
    _SALDO_RE = re.compile(r"^SALDO\s*EM\s*(\d{2})/(\d{2})\s+([\d.]+,\d{2})(-?)$", re.IGNORECASE)

    def parse(self, uploaded_file) -> ExtratoResult:
        if not pdfplumber:
            return ExtratoResult(success=False, erros=["pdfplumber não está instalado."])

        try:
            raw = _read_file_bytes(uploaded_file)
            pdf_file = io.BytesIO(raw)
        except Exception as exc:
            return ExtratoResult(success=False, erros=[f"Erro ao ler PDF: {exc}"])

        try:
            pages = []
            with pdfplumber.open(pdf_file) as pdf:
                for page in pdf.pages:
                    text = page.extract_text(layout=False) or ""
                    pages.append(text)
        except Exception as exc:
            return ExtratoResult(success=False, erros=[f"Erro no pdfplumber: {exc}"])

        full_text = "\n".join(pages)
        lines = [l.strip() for l in full_text.splitlines() if l.strip()]

        header = self._extract_header(full_text)
        
        # Gera os dados brutos e joga no pandas
        dados_brutos = self._extract_raw_data(lines, header.periodo_inicio)
        
        if not dados_brutos:
            return ExtratoResult(
                success=True,
                header=header,
                lancamentos=[],
                total_lancamentos=0,
            )

        # Usando o Pandas para organizar e tipar os dados
        df = pd.DataFrame(dados_brutos)
        
        # Limpeza e Padronização via Pandas
        df['descricao'] = df['descricao'].fillna("").astype(str).str.strip()
        df['documento'] = df['documento'].fillna("").astype(str).str.strip()
        
        # Filtra valores zerados comuns, mantendo linhas informativas de saldo.
        df = df[
            (df['valor_decimal'] != Decimal("0"))
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

    def _extract_header(self, text: str) -> ExtratoHeader:
        h = ExtratoHeader()
        h.dados_brutos = {"banco": "santander"}

        m = re.search(r"Nome\s*\n(.+)", text, re.IGNORECASE)
        if m:
            h.empresa_nome = m.group(1).strip()

        agencia_patterns = [
            r"Ag[êe]ncia\s*[:\-]?\s*\n?\s*(\d+)",
            r"\bAg\.?\s*[:\-]?\s*(\d+)",
        ]
        for pattern in agencia_patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                h.agencia = m.group(1).strip()
                break

        conta_patterns = [
            r"Conta\s+Corrente\s*[:\-]?\s*\n?\s*([\d.\-]+)",
            r"\bConta\s*[:\-]?\s*([\d.\-]+)",
            r"\bC/C\s*[:\-]?\s*([\d.\-]+)",
        ]
        for pattern in conta_patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                h.conta = m.group(1).strip()
                break

        # CNPJ/CPF do titular
        m = re.search(r"CNPJ[:\s]*([\d]{2}[\.\d]{11}[\/]?\d{4}[-]?\d{2})", text, re.IGNORECASE)
        if not m:
            m = re.search(r"\b(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})\b", text)
        if m:
            h.empresa_cnpj = m.group(1).strip()

        # Período
        m = re.search(r"Resumo\s*[-–]\s*(\w+)/(\d{4})", text, re.IGNORECASE)
        if m:
            meses = {
                "janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4,
                "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
                "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
            }
            mes_str = m.group(1).lower()
            ano_str = int(m.group(2))
            if mes_str in meses:
                month = meses[mes_str]
                h.periodo_inicio = date(ano_str, month, 1)
                h.periodo_fim = date(ano_str, month, calendar.monthrange(ano_str, month)[1])

        return h

    def _extract_raw_data(self, lines: list[str], ref_date: date | None) -> list[dict]:
        dados = []
        year = ref_date.year if ref_date else date.today().year

        _END_RE = re.compile(
            r"^(D[eé]bito\s+Autom[aá]tico\s+em\s+Conta"
            r"|Saldos\s+por\s+Per[íi]odo"
            r"|Comprovantes?\s+de\s+Pagamento"
            r"|Cr[eé]ditos\s+Contratados"
            r"|Pacote\s+de\s+Servi[çc]os"
            r"|Programa\s+de\s+Relacionamento"
            r"|[ÍI]ndices\s+Econ[ôo]micos"
            r"|Voc[êe]\s+e\s+Seu\s+Dinheiro"
            r"|ContaMax\s+Empresarial"
            r"|Posi[çc][aã]o\s+Consolidada"
            r"|A\s+gente\s+est[aá]\s+aqui)",
            re.IGNORECASE,
        )

        _SKIP_RE = re.compile(
            r"^(SALDO\s+EM\b"
            r"|Pagina\s*:|Extrato_PJ|BALP_"
            r"|Prezado|Conhe[çc]a|Fale\s+Conosco"
            r"|Central\s+de\s+Atendimento|SAC\s*[-–]|Ouvidoria"
            r"|Redes\s+Sociais|www\.|http|@"
            r"|N[ºo]\s+Documento|Movimentos\s+\(|Saldo\s+\("
            r"|Cr[eé]ditos\s+D[eé]bitos|Data\s+Descri[çc]"
            r"|EXTRATO\s+CONSOLIDADO"
            r"|(?:janeiro|fevereiro|mar[çc]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)/\d{4}"
            r"|Agência\s*$|Conta\s+Corrente\s*$|Nome\s*$"
            r"|Resumo\s*[-–]|Per[íi]odo\s*$|Movimenta[çc][aã]o\s*$)",
            re.IGNORECASE,
        )

        in_movimentacao = False
        current_date: date | None = None
        pending_desc: list[str] = []

        for line in lines:
            if _END_RE.match(line) and in_movimentacao:
                break

            if _SKIP_RE.match(line):
                if "Créditos Débitos" in line or "Créditos Débitos".upper() in line.upper():
                    in_movimentacao = True
                continue

            if ("Conta Corrente" in line or "Créditos Débitos" in line) and not in_movimentacao:
                in_movimentacao = True
                continue

            if not in_movimentacao:
                continue

            saldo_match = re.match(r"^SALDOEM(\d{2})/(\d{2})([\d.]+,\d{2})(-?)$", line.replace(" ", ""), re.IGNORECASE)
            if saldo_match:
                dia, mes, valor_str, sign = saldo_match.groups()
                saldo = _parse_brl_decimal(valor_str)
                if sign == "-":
                    saldo = -saldo
                year_for_saldo = year - 1 if int(mes) > (ref_date.month if ref_date else 12) else year
                natureza = "SALDO_ANTERIOR" if int(mes) != (ref_date.month if ref_date else int(mes)) else "SALDO_FINAL"
                dados.append({
                    "data_obj": date(year_for_saldo, int(mes), int(dia)),
                    "descricao": "SALDO ANTERIOR" if natureza == "SALDO_ANTERIOR" else "SALDO FINAL",
                    "documento": "",
                    "natureza": natureza,
                    "valor_decimal": abs(saldo),
                    "saldo_decimal": saldo,
                })
                continue

            dt_match = self._DATE_RE.match(line)
            if dt_match:
                dt_str = dt_match.group(1)
                rem_line = dt_match.group(2)

                try:
                    d, m_ = dt_str.split("/")
                    current_date = date(year, int(m_), int(d))
                except ValueError:
                    pass

                self._parse_line_into_data(rem_line, current_date, dados, pending_desc)

            else:
                if self._parse_line_into_data(line, current_date, dados, pending_desc, is_continuation=True):
                    pass
                else:
                    pending_desc.append(line)

        return dados

    def _parse_line_into_data(self, text: str, current_date: date | None, dados: list[dict], pending_desc: list[str], is_continuation: bool = False) -> bool:
        matches = list(self._VALUE_RE.finditer(text))
        if not matches:
            return False

        if len(matches) == 2:
            m_val = matches[0]
        else:
            m_val = matches[-1]

        val_str = m_val.group(1)
        sign = m_val.group(2)
        natureza = "DEBITO" if sign == "-" else "CREDITO"
        saldo_dec: Decimal | None = None
        if len(matches) >= 2:
            saldo_match = matches[-1]
            if saldo_match is not m_val:
                saldo_dec = _parse_brl_decimal(saldo_match.group(1))
                if saldo_match.group(2) == "-":
                    saldo_dec = -saldo_dec

        prefix = text[: m_val.start()].strip()
        if not prefix and is_continuation:
            return False

        doc_match = re.search(r"(\d{5,})$", prefix)
        documento = ""
        if doc_match:
            documento = doc_match.group(1)
            prefix = prefix[: doc_match.start()].strip()

        desc_parts = pending_desc + [prefix]
        descricao_final = " ".join([p for p in desc_parts if p])
        pending_desc.clear()

        valor_dec = _parse_brl_decimal(val_str)
        
        dados.append({
            "data_obj": current_date,
            "descricao": descricao_final,
            "documento": documento,
            "natureza": natureza,
            "valor_decimal": valor_dec,
            "valor_str": val_str,
            "saldo_decimal": saldo_dec,
        })
        
        return True
