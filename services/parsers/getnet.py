"""
Parser para extrato Getnet "O que vendi - Consolidado por Data de Vendas".
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from .base import _parse_brl_decimal, _parse_date_br, _read_file_bytes


@dataclass
class GetnetVenda:
    cartao: str = ""
    data_venda: Optional[date] = None
    codigo_estabelecimento: str = ""
    quantidade: int = 0
    valor_bruto: Decimal = field(default_factory=lambda: Decimal("0"))
    valor_liquido: Decimal = field(default_factory=lambda: Decimal("0"))
    valor_tarifa: Decimal = field(default_factory=lambda: Decimal("0"))


@dataclass
class GetnetResumo:
    razao_social: str = ""
    codigo_estabelecimento: str = ""
    cnpj_cpf: str = ""
    periodo_inicio: Optional[date] = None
    periodo_fim: Optional[date] = None
    valor_bruto: Decimal = field(default_factory=lambda: Decimal("0"))
    valor_liquido: Decimal = field(default_factory=lambda: Decimal("0"))
    valor_tarifa: Decimal = field(default_factory=lambda: Decimal("0"))


@dataclass
class GetnetResult:
    success: bool = False
    resumo: GetnetResumo = field(default_factory=GetnetResumo)
    vendas: list[GetnetVenda] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)


def parse_getnet_extrato(uploaded_file) -> GetnetResult:
    try:
        from pypdf import PdfReader
    except ImportError:
        return GetnetResult(success=False, erros=["pypdf não instalado."])

    try:
        raw = _read_file_bytes(uploaded_file)
        reader = PdfReader(io.BytesIO(raw))
    except Exception as exc:
        return GetnetResult(success=False, erros=[f"Erro ao ler PDF: {exc}"])

    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    if not text.strip():
        return GetnetResult(success=False, erros=["Nenhum texto encontrado no PDF."])

    resumo = _parse_resumo(text)
    vendas = _parse_vendas(text)

    if not vendas:
        return GetnetResult(success=False, erros=["Nenhuma venda Getnet encontrada no PDF."])

    return GetnetResult(success=True, resumo=resumo, vendas=vendas)


def _parse_resumo(text: str) -> GetnetResumo:
    resumo = GetnetResumo()

    m = re.search(r"Raz[aã]o Social:\s*(.+)", text, re.IGNORECASE)
    if m:
        resumo.razao_social = m.group(1).strip()

    m = re.search(r"C[oó]d\.\s*Estabelecimento:\s*(\d+)", text, re.IGNORECASE)
    if m:
        resumo.codigo_estabelecimento = m.group(1).strip()

    m = re.search(r"CNPJ/CPF:\s*([\d./-]+)", text, re.IGNORECASE)
    if m:
        resumo.cnpj_cpf = m.group(1).strip()

    m = re.search(r"Per[íi]odo:\s*(\d{2}/\d{2}/\d{4})\s*a\s*(\d{2}/\d{2}/\d{4})", text, re.IGNORECASE)
    if m:
        resumo.periodo_inicio = _parse_date_br(m.group(1))
        resumo.periodo_fim = _parse_date_br(m.group(2))

    m = re.search(
        r"Valor Bruto:\s*R\$\s*([\d.,]+)\s+Valor da Taxa\s+e/ou Tarifa:\s*-?\s*R\$\s*([\d.,]+)\s+Valor\s+L[íi]quido:\s*R\$\s*([\d.,]+)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        resumo.valor_bruto = _parse_brl_decimal(m.group(1))
        resumo.valor_tarifa = _parse_brl_decimal(m.group(2))
        resumo.valor_liquido = _parse_brl_decimal(m.group(3))

    return resumo


def _parse_vendas(text: str) -> list[GetnetVenda]:
    vendas: list[GetnetVenda] = []
    line_re = re.compile(
        r"^(?P<cartao>[A-ZÁÉÍÓÚÂÊÔÃÕÇ ]+?)\s+"
        r"(?P<data>\d{2}/\d{2}/\d{4})"
        r"(?P<codigo>\d{6,})\s+"
        r"R\$\s*(?P<bruto>[\d.]+,\d{2})\s+"
        r"R\$\s*(?P<liquido>[\d.]+,\d{2})\s*"
        r"-\s*R\$\s*(?P<tarifa>[\d.]+,\d{2})(?P<quantidade>\d+)$"
    )

    for raw_line in text.splitlines():
        line = raw_line.strip()
        m = line_re.match(line)
        if not m:
            continue
        vendas.append(
            GetnetVenda(
                cartao=m.group("cartao").strip(),
                data_venda=_parse_date_br(m.group("data")),
                codigo_estabelecimento=m.group("codigo").strip(),
                quantidade=int(m.group("quantidade")),
                valor_bruto=_parse_brl_decimal(m.group("bruto")),
                valor_liquido=_parse_brl_decimal(m.group("liquido")),
                valor_tarifa=_parse_brl_decimal(m.group("tarifa")),
            )
        )

    return vendas
