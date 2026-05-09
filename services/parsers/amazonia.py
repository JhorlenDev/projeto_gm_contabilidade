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

        # Injetar Saldo Anterior e Saldo Final como lançamentos especiais
        ref_date = header.periodo_inicio
        if lancamentos and not ref_date:
            ref_date = lancamentos[0].data

        if ref_date and header.saldo is not None and str(header.dados_brutos.get("saldo_anterior", "")) != "0":
            lancamentos.insert(0, LancamentoExtrato(
                linha_origem=0,
                pagina=1,
                data=ref_date,
                descricao_original="Saldo Disponível Inicial",
                documento="",
                valor=header.saldo,
                natureza_inferida="SALDO_ANTERIOR",
                saldo=header.saldo,
                linha_original="",
            ))

        if lancamentos:
            ultimo = next((l for l in reversed(lancamentos) if l.natureza_inferida not in ("SALDO_ANTERIOR", "SALDO_FINAL")), None)
            if ultimo and ultimo.saldo is not None:
                saldo_final = ultimo.saldo
                # Atualizar header.saldo para refletir o saldo final real
                header.saldo = saldo_final
                header.dados_brutos["saldo_final"] = str(saldo_final)
                lancamentos.append(LancamentoExtrato(
                    linha_origem=9999,
                    pagina=1,
                    data=ultimo.data or ref_date,
                    descricao_original="Saldo Final",
                    documento="",
                    valor=saldo_final,
                    natureza_inferida="SALDO_FINAL",
                    saldo=saldo_final,
                    linha_original="",
                ))

        return ExtratoResult(
            success=True,
            header=header,
            lancamentos=lancamentos,
            total_lancamentos=len([l for l in lancamentos if l.natureza_inferida not in ("SALDO_ANTERIOR", "SALDO_FINAL")]),
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
            h.dados_brutos["saldo_anterior"] = str(h.saldo)

        m = re.search(r"Total\s+de\s+d[eé]bito[:\s]*([\d.,]+)", text, re.IGNORECASE)
        if m:
            h.dados_brutos["total_debito"] = str(_parse_brl_decimal(m.group(1)))

        m = re.search(r"Total\s+de\s+cr[eé]dito[:\s]*([\d.,]+)", text, re.IGNORECASE)
        if m:
            h.dados_brutos["total_credito"] = str(_parse_brl_decimal(m.group(1)))

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
        block_done = False  # True quando já coletamos SALDO (6ª linha após DATA)

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
                # Um bloco BASA tem exatamente: NR_DOC, HISTÓRICO, VALOR, D/C, SALDO
                # Quando a última linha acumulada é o SALDO (linha após D/C), o bloco está completo.
                # Detectamos: penúltima linha é "D" ou "C" e última é um número decimal.
                if len(current_extra) >= 4:
                    penultima = current_extra[-2] if len(current_extra) >= 2 else ""
                    ultima = current_extra[-1]
                    if _DC_LINE_RE.match(penultima) and self._VALUE_RE.fullmatch(ultima.lstrip("-")):
                        block_done = True

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
