"""
Parser para extrato Banco do Brasil (Conta Corrente).
Utiliza pdfplumber e pandas para extrair e organizar os dados.
Formato: Dt. balancete | Dt. movimento | Ag. | Lote | Histórico | Documento | Valor C/D | Saldo
O valor aparece como "120,00 C" ou "520,52 D" na mesma célula.
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

    _DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+(\d{4})\s+(\d+)\s+(.+)$")
    _VALUE_DC_RE = re.compile(r"([\d.]+,\d{2})\s+([CD])\s*$")
    _ALL_VALUES_RE = re.compile(r"([\d.]+,\d{2})\s+([CD])")
    _SALDO_ANT_RE = re.compile(r"Saldo\s+Anterior", re.IGNORECASE)
    _BB_RENDE_RE = re.compile(r"BB\s+Rende", re.IGNORECASE)
    _SALDO_FINAL_RE = re.compile(r"S\s+A\s+L\s+D\s+O|Saldo\s+Final", re.IGNORECASE)

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
        
        # Gera os dados brutos
        dados_brutos = self._extract_raw_data(lines)
        
        if not dados_brutos:
            return ExtratoResult(
                success=True,
                header=header,
                lancamentos=[],
                total_lancamentos=0,
            )

        # Usando Pandas para organizar os dados
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
                linha_origem=int(row.get('linha_origem') or 0),
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
        h.dados_brutos = {"banco": "bb"}

        m = re.search(r"Conta\s+corrente:\s*([\d\s./-]+)", text, re.IGNORECASE)
        if m:
            h.conta = m.group(1).strip()

        m = re.search(r"Per[íi]odo do extrato\s+(\d{2}\s*/\s*\d{4})", text, re.IGNORECASE)
        if m:
            mm, yyyy = m.group(1).split("/")
            year = int(yyyy.strip())
            month = int(mm.strip())
            h.periodo_inicio = date(year, month, 1)
            h.periodo_fim = date(year, month, calendar.monthrange(year, month)[1])

        return h

    def _extract_raw_data(self, lines: list[str]) -> list[dict]:
        dados = []
        in_lancamentos = False
        
        current_data = None
        current_doc = ""
        current_hist: list[str] = []
        current_val_dec: Decimal | None = None
        current_natureza = ""
        current_saldo_dec: Decimal | None = None
        current_linha = 0

        def _flush_lancamento():
            nonlocal current_data, current_doc, current_hist, current_val_dec, current_natureza, current_saldo_dec, current_linha
            natureza_especial = current_natureza in {"SALDO_ANTERIOR", "SALDO_FINAL"}
            if current_data and current_val_dec is not None and (current_val_dec > 0 or natureza_especial):
                desc_final = " ".join(current_hist).strip()
                dados.append({
                    "data_obj": current_data,
                    "descricao": desc_final,
                    "documento": current_doc,
                    "natureza": current_natureza,
                    "valor_decimal": current_val_dec,
                    "saldo_decimal": current_saldo_dec,
                    "linha_origem": current_linha,
                })

            current_data = None
            current_doc = ""
            current_hist.clear()
            current_val_dec = None
            current_natureza = ""
            current_saldo_dec = None
            current_linha = 0

        for line_number, line in enumerate(lines, start=1):
            if "Dt. balancete" in line or "Dt. movimento" in line or "Lançamentos" in line:
                in_lancamentos = True
                continue

            if not in_lancamentos:
                continue

            m_dt = self._DATE_RE.match(line)
            if m_dt:
                _flush_lancamento()

                dt_str = m_dt.group(1)
                lote = m_dt.group(2)
                codigo = m_dt.group(3)
                remainder = m_dt.group(4).strip()

                current_data = _parse_date_br(dt_str)
                current_linha = line_number

                all_vals = list(self._ALL_VALUES_RE.finditer(remainder))
                if not all_vals:
                    current_hist.append(remainder)
                    continue

                # Se houver mais de um (ex: valor e saldo na mesma linha), pega o primeiro
                v_match = all_vals[0]
                val_str = v_match.group(1)
                cd = v_match.group(2).upper()
                
                current_val_dec = _parse_brl_decimal(val_str)
                current_natureza = "CREDITO" if cd == "C" else "DEBITO"
                if len(all_vals) > 1:
                    current_saldo_dec = _parse_brl_decimal(all_vals[1].group(1))
                    if all_vals[1].group(2).upper() == "D":
                        current_saldo_dec = -current_saldo_dec
                else:
                    current_saldo_dec = None

                texto_antes_valor = remainder[: v_match.start()].strip()

                if self._SALDO_ANT_RE.search(remainder):
                    current_natureza = "SALDO_ANTERIOR"
                    current_saldo_dec = current_val_dec
                    current_hist.append("SALDO ANTERIOR")
                    continue

                if self._SALDO_FINAL_RE.search(remainder):
                    current_natureza = "SALDO_FINAL"
                    current_saldo_dec = current_val_dec
                    current_hist.append("SALDO FINAL")
                    _flush_lancamento()
                    break
                
                doc_m = re.search(r"([\d.\-]+)$", texto_antes_valor)
                if doc_m:
                    current_doc = doc_m.group(1)
                    texto_antes_valor = texto_antes_valor[: doc_m.start()].strip()

                current_hist.append(texto_antes_valor)
                if self._BB_RENDE_RE.search(texto_antes_valor) and current_saldo_dec is None:
                    current_saldo_dec = -current_val_dec if cd == "D" else current_val_dec

            else:
                if current_data is not None:
                    if self._ALL_VALUES_RE.search(line) and not "C" in line and not "D" in line:
                         pass
                    else:
                         current_hist.append(line.strip())

        _flush_lancamento()
        return dados
