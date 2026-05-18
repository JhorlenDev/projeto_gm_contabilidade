"""
Parser para extrato Caixa Econômica Federal (Gerenciador Caixa).
Formato: Data | Mov. | Nr. Doc. | Histórico | Valor | Saldo
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


class CaixaExtratoParser:
    """
    Extrato por período da Caixa Econômica Federal.
    Layout: 02/01/2024 291156 ENVIO TEV 1.000,00 D 1.000,00 D
    """

    # Data + Doc + Historico + Valor + Natureza + Saldo + NatSaldo
    _LINE_RE = re.compile(
        r"^(\d{2}/\d{2}/\d{4})\s+(\d+)\s+(.+?)\s+([\d.]+,\d{2})\s+([CD])\s+([\d.]+,\d{2})\s+([CD])$"
    )
    # Linha de Saldo do Dia (apenas um valor)
    _SALDO_DIA_RE = re.compile(
        r"^(\d{2}/\d{2}/\d{4})\s+(\d+)\s+SALDO DIA\s+([\d.]+,\d{2})\s+([CD])$"
    )

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
                    text = page.extract_text() or ""
                    pages.append(text)
        except Exception as exc:
            return ExtratoResult(success=False, erros=[f"Erro no pdfplumber: {exc}"])

        full_text = "\n".join(pages)
        lines = [l.strip() for l in full_text.splitlines() if l.strip()]

        header = self._extract_header(full_text)
        dados_brutos = self._extract_raw_data(lines)

        if not dados_brutos:
            return ExtratoResult(
                success=True,
                header=header,
                lancamentos=[],
                total_lancamentos=0,
            )

        df = pd.DataFrame(dados_brutos)
        df['natureza'] = df['natureza'].fillna("").astype(str).str.strip()
        if header.periodo_inicio:
            df.loc[(df['natureza'] == "SALDO_ANTERIOR") & (df['data_obj'].isna()), 'data_obj'] = header.periodo_inicio

        saldo_dia = df[df['natureza'] == "SALDO_DIA"]
        if not saldo_dia.empty:
            last_idx = saldo_dia.index[-1]
            df.loc[last_idx, 'natureza'] = "SALDO_FINAL"
            df.loc[last_idx, 'descricao'] = "SALDO FINAL"
            df.loc[last_idx, 'valor_decimal'] = abs(df.loc[last_idx, 'saldo_decimal'])

        df = df[df['natureza'] != "SALDO_DIA"]

        lancamentos = []
        for _, row in df.iterrows():
            lancamentos.append(LancamentoExtrato(
                data=row['data_obj'],
                descricao_original=row['descricao'],
                valor=row['valor_decimal'],
                natureza_inferida=row['natureza'],
                documento=row['documento'],
                saldo=row['saldo_decimal']
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
        h.dados_brutos = {"banco": "caixa"}

        m = re.search(r"Cliente:\s*(.+)", text, re.IGNORECASE)
        if m:
            h.empresa_nome = m.group(1).strip()

        m = re.search(r"Conta:\s*([\d\s|/-]+)", text, re.IGNORECASE)
        if m:
            h.conta = m.group(1).strip()

        m = re.search(r"Mês:\s*(.+)", text, re.IGNORECASE)
        if m:
            mes_referencia = m.group(1).strip()
            h.dados_brutos["mes_referencia"] = mes_referencia
            meses = {
                "janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4,
                "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
                "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
            }
            m_mes = re.search(r"([A-Za-zçÇ]+)\s*/\s*(\d{4})", mes_referencia)
            if m_mes:
                mes = meses.get(m_mes.group(1).lower())
                ano = int(m_mes.group(2))
                if mes:
                    h.periodo_inicio = date(ano, mes, 1)
                    h.periodo_fim = date(ano, mes, calendar.monthrange(ano, mes)[1])

        return h

    def _extract_raw_data(self, lines: list[str]) -> list[dict]:
        dados = []
        in_extrato = False

        for line in lines:
            if "Data Mov. Nr. Doc. Histórico Valor Saldo" in line:
                in_extrato = True
                continue
            
            if not in_extrato:
                continue

            # Tenta linha normal
            m = self._LINE_RE.match(line)
            if m:
                dt_str, doc, hist, val_str, nat, saldo_str, natsaldo = m.groups()
                saldo = _parse_brl_decimal(saldo_str)
                if natsaldo == "D":
                    saldo = -saldo
                dados.append({
                    "data_obj": _parse_date_br(dt_str),
                    "documento": doc,
                    "descricao": hist.strip(),
                    "valor_decimal": _parse_brl_decimal(val_str),
                    "natureza": "CREDITO" if nat == "C" else "DEBITO",
                    "saldo_decimal": saldo
                })
                continue

            # Tenta linha de saldo dia
            m_s = self._SALDO_DIA_RE.match(line)
            if m_s:
                dt_str, doc, saldo_str, nat = m_s.groups()
                saldo = _parse_brl_decimal(saldo_str)
                if nat == "D":
                    saldo = -saldo
                dados.append({
                    "data_obj": _parse_date_br(dt_str),
                    "documento": doc,
                    "descricao": "SALDO DIA",
                    "valor_decimal": Decimal("0"),
                    "natureza": "SALDO_DIA",
                    "saldo_decimal": saldo
                })
                continue
            
            # Caso especial: SALDO ANTERIOR
            if "SALDO ANTERIOR" in line:
                # 000000 SALDO ANTERIOR 0,00 0,00
                parts = line.split()
                if len(parts) >= 4:
                    # Tenta pegar os dois últimos como valores
                    val1 = parts[-2]
                    val2 = parts[-1]
                    dados.append({
                        "data_obj": None,
                        "documento": parts[0],
                        "descricao": "SALDO ANTERIOR",
                        "valor_decimal": Decimal("0"),
                        "natureza": "SALDO_ANTERIOR",
                        "saldo_decimal": _parse_brl_decimal(val2)
                    })

        return dados
