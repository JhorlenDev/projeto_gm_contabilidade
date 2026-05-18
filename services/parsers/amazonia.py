"""
Parser para extrato Banco da Amazônia (GESOP / PD_CCOR).
Formato: DATA | NR DOC | HISTÓRICO | VALOR LANCTO | D/C | SALDO
Utiliza pdfplumber e pandas para extrair e organizar os dados.
"""
from __future__ import annotations

import io
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
    _parse_date_br,
    _read_file_bytes,
)


class AmazoniaExtratoParser:
    """
    Extrato mensal do Banco da Amazônia — sistema GESOP.
    """

    _DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{2,4})(?:\s+(.+))?$")
    _VALUE_RE = re.compile(r"-?[\d.]+,\d{2}")
    _DC_RE = re.compile(r"([\d.,]+)\s+([DC])(?:\s+[\d.,]+|\s*$)")

    def parse(self, uploaded_file) -> ExtratoResult:
        if not pdfplumber:
            return ExtratoResult(success=False, erros=["pdfplumber não instalado."])

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
        dados_brutos = self._extract_raw_data(lines)

        # Injetar Saldo Anterior se houver
        ref_date = header.periodo_inicio
        if dados_brutos and not ref_date:
            ref_date = dados_brutos[0].get("data_obj")

        if ref_date and header.saldo is not None and str(header.dados_brutos.get("saldo_anterior", "")) != "0":
            dados_brutos.insert(0, {
                "data_obj": ref_date,
                "descricao": "Saldo Disponível Inicial",
                "documento": "",
                "valor_decimal": header.saldo,
                "natureza": "SALDO_ANTERIOR",
                "saldo_decimal": header.saldo,
            })

        ultimo_com_saldo = next((row for row in reversed(dados_brutos) if row.get("saldo_decimal") is not None), None)
        if ultimo_com_saldo:
            saldo_final = ultimo_com_saldo["saldo_decimal"]
            dados_brutos.append({
                "data_obj": ultimo_com_saldo.get("data_obj"),
                "descricao": "Saldo Final",
                "documento": "",
                "valor_decimal": abs(saldo_final),
                "natureza": "SALDO_FINAL",
                "saldo_decimal": saldo_final,
            })

        if not dados_brutos:
            return ExtratoResult(
                success=True,
                header=header,
                lancamentos=[],
                total_lancamentos=0,
            )

        df = pd.DataFrame(dados_brutos)

        df['descricao'] = df['descricao'].fillna("").astype(str).str.strip()
        df['documento'] = df['documento'].fillna("").astype(str).str.strip()
        df['natureza'] = df['natureza'].fillna("").astype(str).str.strip()

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

    def _extract_header(self, text: str) -> ExtratoHeader:
        h = ExtratoHeader()
        h.dados_brutos = {"banco": "amazonia"}

        m = re.search(r"Titular\s*:\s*([\d./\-]+)\s*-\s*(.+)", text, re.IGNORECASE)
        if m:
            h.empresa_cnpj = m.group(1).strip()
            h.empresa_nome = m.group(2).strip().split("\n")[0].strip()

        agencia_patterns = [
            r"Ag[êe]ncia\s*[:\-]?\s*(\d+)",
            r"\bAg\.?\s*[:\-]?\s*(\d+)",
        ]
        for pattern in agencia_patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                h.agencia = m.group(1).strip()
                break

        conta_patterns = [
            r"Conta\s*[:\-]?\s*([\d.\-]+)",
            r"\bC/C\s*[:\-]?\s*([\d.\-]+)",
        ]
        for pattern in conta_patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                h.conta = m.group(1).strip()
                break

        m = re.search(r"Saldo\s+Dispon[íi]vel\s+Inicial[:\s]*([\d.,]+)", text, re.IGNORECASE)
        if m:
            h.saldo = _parse_brl_decimal(m.group(1))
            h.dados_brutos["saldo_anterior"] = str(h.saldo)

        m = re.search(r"Total\s+de\s+d[eé]bito[:\s]*([\d.,]+)", text, re.IGNORECASE)
        if m:
            h.dados_brutos["total_debito"] = str(_parse_brl_decimal(m.group(1)))

        m = re.search(r"Total\s+de\s+cr[eé]dito[:\s]*([\d.,]+)", text, re.IGNORECASE)
        if m:
            h.dados_brutos["total_credito"] = str(_parse_brl_decimal(m.group(1)))

        m = re.search(r"(\d{2})\s*/\s*(\d{4})", text)
        if m:
            try:
                h.periodo_inicio = date(int(m.group(2)), int(m.group(1)), 1)
                import calendar
                last_day = calendar.monthrange(int(m.group(2)), int(m.group(1)))[1]
                h.periodo_fim = date(int(m.group(2)), int(m.group(1)), last_day)
            except Exception:
                pass

        return h

    def _extract_raw_data(self, lines: list[str]) -> list[dict]:
        dados = []

        _SKIP_RE = re.compile(
            r"^(Total\s+de|Data\s+da|Hora\s+da|Emitido|Para\s+simples|Vencto|Tipo\s+Conta"
            r"|DATA\s*$|DATA\s+NR|NR\s+DOC|HISTÓRICO|VALOR\s+LANCTO|D/C\s*$|SALDO\s*$"
            r"|Saldo\s+Dispon|PD_CCOR|GESOP|Agência\s*:|Conta\s*:|Titular\s*:|Limite\s*:"
            r"|IBAN_|Emitir\s+Extrato|\d{2}\s*/\s*\d{4}\s*$|1\s+de\s+\d|Extrato_mes"
            r"|DP\s+PJ|LTDA\s*$)",
            re.IGNORECASE,
        )

        blocks: list[tuple[str, str, list[str]]] = []
        current_date: str | None = None
        current_first: str = ""
        current_extra: list[str] = []
        block_done = False

        _DC_LINE_RE = re.compile(r"^[DC]$")

        for line in lines:
            if _SKIP_RE.search(line):
                continue
            m = self._DATE_RE.match(line)
            if m:
                if current_date is not None:
                    blocks.append((current_date, current_first, current_extra))
                current_date = m.group(1)
                current_first = (m.group(2) or "").strip()
                current_extra = []
                block_done = False
            elif current_date is not None and not block_done:
                current_extra.append(line)
                if len(current_extra) >= 4:
                    penultima = current_extra[-2] if len(current_extra) >= 2 else ""
                    ultima = current_extra[-1]
                    if _DC_LINE_RE.match(penultima) and self._VALUE_RE.fullmatch(ultima.lstrip("-")):
                        block_done = True

        if current_date is not None:
            blocks.append((current_date, current_first, current_extra))

        for date_str, first_rest, extra_lines in blocks:
            data = _parse_date_br(date_str)
            if not data:
                continue

            full = " ".join([first_rest] + extra_lines).strip()
            if not full:
                continue

            values = self._VALUE_RE.findall(full)
            if not values:
                continue

            saldo_str = values[-1]
            valor_str = values[-2] if len(values) >= 2 else values[-1]

            valor = abs(_parse_brl_decimal(valor_str))

            if valor <= 0:
                continue

            dc_match = self._DC_RE.search(full)
            if dc_match:
                natureza = "DEBITO" if dc_match.group(2) == "D" else "CREDITO"
            elif valor_str.startswith("-"):
                natureza = "DEBITO"
            else:
                natureza = ""

            first_val_pos = full.find(values[0])
            desc_raw = full[:first_val_pos].strip() if first_val_pos > 0 else full

            documento = ""
            doc_m = re.match(r"^(\d{4,})\s+", desc_raw)
            if doc_m:
                documento = doc_m.group(1)
                desc_raw = desc_raw[doc_m.end():].strip()

            dados.append({
                "data_obj": data,
                "descricao": desc_raw,
                "documento": documento,
                "valor_decimal": valor,
                "natureza": natureza,
                "saldo_decimal": _parse_brl_decimal(saldo_str),
            })

        return dados
