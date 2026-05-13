"""
Parser para extrato Banco do Brasil (Conta Corrente).
Formato: Dt. balancete | Dt. movimento | Ag. | Lote | Histórico | Documento | Valor C/D | Saldo
O valor aparece como "120,00 C" ou "520,52 D" na mesma célula.
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


class BancoBrasilExtratoParser:
    """
    Extrato Conta Corrente do Banco do Brasil.
    pypdf extrai as células em linhas; cada lançamento pode ocupar 2 linhas:
      linha 1: 02/01/2024 0000 14397 821 Pix-Recebido QR Code 4.980.512.658 120,00 C
      linha 2: 30/12 10:53 00077668340220 Lucicleide
    Identificamos pelo padrão: data + lote + código + histórico + doc + valor + C/D
    """

    _DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+(\d{4})\s+(\d+)\s+(\d+)\s+(.+)$")
    _VALUE_DC_RE = re.compile(r"([\d.]+,\d{2})\s+([CD])\s*$")
    _VALUE_DC_INLINE = re.compile(r"([\d.]+,\d{2})\s+([CD])\b")
    _ALL_VALUES_RE = re.compile(r"([\d.]+,\d{2})\s+([CD])")
    _SALDO_ANT_RE = re.compile(r"Saldo\s+Anterior", re.IGNORECASE)
    _BB_RENDE_RE = re.compile(r"BB\s+Rende", re.IGNORECASE)
    _SALDO_FINAL_RE = re.compile(r"S\s*A\s*L\s*D\s*O", re.IGNORECASE)

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
        h.dados_brutos = {"banco": "bb"}

        m = re.search(r"Conta corrente\s+([\w\s\-]+)\n", text, re.IGNORECASE)
        if m:
            h.empresa_nome = m.group(1).strip()

        agencia_patterns = [
            r"Ag[êe]ncia\s*[:\-]?\s*([\d\-]+)",
            r"\bAg\.?\s*[:\-]?\s*([\d\-]+)",
        ]
        for pattern in agencia_patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                h.agencia = m.group(1).strip()
                break

        conta_patterns = [
            r"Conta\s+corrente\s*[:\-]?\s*([\d.\-]+[A-Z]?)",
            r"\bConta\s*[:\-]?\s*([\d.\-]+[A-Z]?)",
            r"\bC/C\s*[:\-]?\s*([\d.\-]+[A-Z]?)",
        ]
        for pattern in conta_patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                h.conta = m.group(1).strip()
                break

        m = re.search(r"CPF/CNPJ[:\s]*([\d.\/\-]+)", text, re.IGNORECASE)
        if not m:
            m = re.search(r"CNPJ[:\s]*([\d]{2}[\.\d]{11}[\/]?\d{4}[-]?\d{2})", text, re.IGNORECASE)
        if not m:
            m = re.search(r"\b(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})\b", text)
        if m:
            h.empresa_cnpj = m.group(1).strip()

        m = re.search(r"Per[íi]odo do extrato\s+(\d{2}\s*/\s*\d{4})", text, re.IGNORECASE)
        if m:
            ref = m.group(1).replace(" ", "")
            parts = ref.split("/")
            if len(parts) == 2:
                try:
                    import calendar
                    month, year = int(parts[0]), int(parts[1])
                    h.periodo_inicio = date(year, month, 1)
                    h.periodo_fim = date(year, month, calendar.monthrange(year, month)[1])
                except Exception:
                    pass

        m = re.search(r"Saldo\s+Anterior\s+([\d.,]+)\s*([CD])", text, re.IGNORECASE)
        if m:
            h.saldo = _parse_brl_decimal(m.group(1))

        return h

    def _extract_lancamentos(self, lines: list[str]) -> list[LancamentoExtrato]:
        lancamentos = []
        line_idx = 0

        _SKIP_RE = re.compile(
            r"^(Dt\.\s+balancete|Lançamentos|Cliente\s*-|Agência|Conta corrente|Per[íi]odo|"
            r"Consultas\s*-|G\d{15}|Transação\s+efetuada"
            r"|Serviço\s+de\s+Atendimento|Para\s+deficientes|Ouvidoria|SAC\s*[0-9])",
            re.IGNORECASE,
        )

        i = 0
        while i < len(lines):
            line = lines[i]
            if _SKIP_RE.search(line):
                i += 1
                continue

            m = self._DATE_RE.match(line)
            if not m:
                i += 1
                continue

            date_str = m.group(1)
            data = _parse_date_br(date_str)
            if not data:
                i += 1
                continue

            rest = m.group(5).strip()

            # ── Linhas especiais de saldo ────────────────────────────────
            if self._SALDO_ANT_RE.search(rest):
                all_vals = self._ALL_VALUES_RE.findall(rest)
                if all_vals:
                    v_str, dc = all_vals[0]
                    valor_sp = _parse_brl_decimal(v_str)
                    saldo_sp = valor_sp if dc == "C" else -valor_sp
                else:
                    valor_sp, saldo_sp = _parse_brl_decimal("0"), _parse_brl_decimal("0")
                line_idx += 1
                lancamentos.append(LancamentoExtrato(
                    linha_origem=line_idx,
                    pagina=1,
                    data=data,
                    descricao_original="Saldo Anterior",
                    documento="",
                    valor=valor_sp,
                    natureza_inferida="SALDO_ANTERIOR",
                    saldo=saldo_sp,
                    linha_original=line,
                ))
                i += 1
                continue

            if self._SALDO_FINAL_RE.search(rest):
                all_vals = self._ALL_VALUES_RE.findall(rest)
                if all_vals:
                    v_str, dc = all_vals[0]
                    valor_sp = _parse_brl_decimal(v_str)
                    saldo_sp = valor_sp if dc == "C" else -valor_sp
                else:
                    valor_sp, saldo_sp = _parse_brl_decimal("0"), _parse_brl_decimal("0")
                line_idx += 1
                lancamentos.append(LancamentoExtrato(
                    linha_origem=line_idx,
                    pagina=1,
                    data=data,
                    descricao_original="Saldo Final",
                    documento="",
                    valor=valor_sp,
                    natureza_inferida="SALDO_FINAL",
                    saldo=saldo_sp,
                    linha_original=line,
                ))
                i += 1
                continue

            if self._BB_RENDE_RE.search(rest):
                all_vals = self._ALL_VALUES_RE.findall(rest)
                if len(all_vals) >= 2:
                    # Primeiro par = movimento, último par = saldo do dia
                    v_str, dc = all_vals[0]
                    valor_sp = _parse_brl_decimal(v_str)
                    natureza_sp = "CREDITO" if dc == "C" else "DEBITO"
                    s_str, s_dc = all_vals[-1]
                    saldo_sp = _parse_brl_decimal(s_str)
                    if s_dc == "D":
                        saldo_sp = -saldo_sp
                elif len(all_vals) == 1:
                    v_str, dc = all_vals[0]
                    valor_sp = _parse_brl_decimal(v_str)
                    natureza_sp = "CREDITO" if dc == "C" else "DEBITO"
                    saldo_sp = valor_sp if dc == "C" else -valor_sp
                else:
                    i += 1
                    continue
                line_idx += 1
                lancamentos.append(LancamentoExtrato(
                    linha_origem=line_idx,
                    pagina=1,
                    data=data,
                    descricao_original="BB Rende Fácil",
                    documento="9903",
                    valor=valor_sp,
                    natureza_inferida=natureza_sp,
                    saldo=saldo_sp,
                    linha_original=line,
                ))
                i += 1
                continue
            # ── Fim linhas especiais ─────────────────────────────────────

            complemento = ""
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                if re.match(r"^\d{2}/\d{2}\s+\d{2}:\d{2}", next_line):
                    complemento = next_line
                    i += 1
                elif re.match(r"^\d{11,14}\s+", next_line) or re.match(r"^[A-Z]{2,}", next_line):
                    complemento = next_line
                    i += 1

            dc_m = self._VALUE_DC_RE.search(rest)
            if not dc_m:
                dc_m = self._VALUE_DC_INLINE.search(rest)
            if not dc_m:
                i += 1
                continue

            valor_str = dc_m.group(1)
            dc = dc_m.group(2)
            valor = _parse_brl_decimal(valor_str)
            if valor <= 0:
                i += 1
                continue

            natureza = "CREDITO" if dc == "C" else "DEBITO"

            before_val = rest[:dc_m.start()].strip()
            documento = ""
            doc_m = re.search(r"((?:\d{1,3}\.)*\d{3,}|\d{7,})\s*$", before_val)
            if doc_m:
                raw_doc = doc_m.group(1)
                clean_doc = raw_doc.replace(".", "")
                # Mínimo 4 dígitos para cobrir docs curtos do BB (ex: 9.903 → 9903, 10.201 → 10201)
                if len(clean_doc) >= 4 and clean_doc.isdigit():
                    documento = clean_doc
                    before_val = before_val[:doc_m.start()].strip()

            desc = before_val
            if complemento:
                nome_m = re.search(r"\d{2}:\d{2}\s+\d+\s+(.+)", complemento)
                if nome_m:
                    desc = f"{desc} — {nome_m.group(1).strip()}"

            line_idx += 1
            lancamentos.append(LancamentoExtrato(
                linha_origem=line_idx,
                pagina=1,
                data=data,
                descricao_original=desc.strip(),
                documento=documento,
                valor=valor,
                natureza_inferida=natureza,
                saldo=None,
                linha_original=line,
            ))

            i += 1

        lancamentos.sort(key=lambda x: (x.data or date.max, x.linha_origem))
        return lancamentos
