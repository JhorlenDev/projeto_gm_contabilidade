"""
Parser para extrato Banco da Amazônia (GESOP / PD_CCOR).
Formato: DATA | NR DOC | HISTÓRICO | VALOR LANCTO | D/C | SALDO
"""
from __future__ import annotations

import io
import re
from datetime import date

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
    Layout esperado após extração pypdf:
      02/01/24 026577 1127 - AUTOMATIZACAO TARIFA MANUTENCAO PJ -45,00 D 40.318,30
    Aceita anos com 2 ou 4 dígitos. Suporta extração por bloco (cada coluna em linha separada).
    """

    # Aceita DD/MM/YY e DD/MM/YYYY
    _DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{2,4})(?:\s+(.+))?$")
    _VALUE_RE = re.compile(r"-?[\d.]+,\d{2}")
    # D/C pode estar no final da linha (sem trailing number)
    _DC_RE = re.compile(r"([\d.,]+)\s+([DC])(?:\s+[\d.,]+|\s*$)")

    def parse(self, uploaded_file) -> ExtratoResult:
        try:
            from pypdf import PdfReader
        except ImportError:
            return ExtratoResult(success=False, erros=["pypdf não instalado."])
        try:
            reader = PdfReader(io.BytesIO(_read_file_bytes(uploaded_file)))
        except Exception as exc:
            return ExtratoResult(success=False, erros=[f"Erro ao ler PDF: {exc}"])

        pages = [p.extract_text() or "" for p in reader.pages]
        full_text = "\n".join(pages)
        lines = [l.strip() for l in full_text.splitlines() if l.strip()]

        header = self._extract_header(full_text)
        lancamentos = self._extract_lancamentos(lines)

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

        m = re.search(r"Agência\s*:\s*(\d+)", text, re.IGNORECASE)
        if m:
            h.agencia = m.group(1)

        m = re.search(r"Conta\s*:\s*([\d\-]+)", text, re.IGNORECASE)
        if m:
            h.conta = m.group(1)

        m = re.search(r"Saldo\s+Dispon[íi]vel\s+Inicial[:\s]*([\d.,]+)", text, re.IGNORECASE)
        if m:
            h.saldo = _parse_brl_decimal(m.group(1))

        # Período da referência (ex: "01 / 2024")
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

    def _extract_lancamentos(self, lines: list[str]) -> list[LancamentoExtrato]:
        """
        Acumula linhas por bloco de data para suportar tanto o layout em linha única
        quanto o layout multi-linha onde pypdf extrai cada coluna em linhas separadas.
        """
        lancamentos = []

        _SKIP_RE = re.compile(
            r"^(Total\s+de|Data\s+da|Hora\s+da|Emitido|Para\s+simples|Vencto|Tipo\s+Conta"
            r"|DATA\s+NR|Saldo\s+Dispon|PD_CCOR|GESOP)",
            re.IGNORECASE,
        )

        blocks: list[tuple[str, str, list[str]]] = []
        current_date: str | None = None
        current_first: str = ""
        current_extra: list[str] = []

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
            elif current_date is not None:
                current_extra.append(line)

        if current_date is not None:
            blocks.append((current_date, current_first, current_extra))

        line_idx = 0
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

            saldo = _parse_brl_decimal(saldo_str)
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

            line_idx += 1
            lancamentos.append(LancamentoExtrato(
                linha_origem=line_idx,
                pagina=1,
                data=data,
                descricao_original=desc_raw,
                documento=documento,
                valor=valor,
                natureza_inferida=natureza,
                saldo=saldo,
                linha_original=first_rest or " ".join(extra_lines[:2]),
            ))

        lancamentos.sort(key=lambda x: (x.data or date.max, x.linha_origem))
        return lancamentos
