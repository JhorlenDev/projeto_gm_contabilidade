"""
Parser para extrato Cielo "Detalhado de vendas Cielo".
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
class CieloVenda:
    cartao: str = ""
    data_venda: Optional[date] = None
    codigo_estabelecimento: str = ""
    quantidade: int = 1
    valor_bruto: Decimal = field(default_factory=lambda: Decimal("0"))
    valor_liquido: Decimal = field(default_factory=lambda: Decimal("0"))
    valor_tarifa: Decimal = field(default_factory=lambda: Decimal("0"))


@dataclass
class CieloResumo:
    razao_social: str = ""
    codigo_estabelecimento: str = ""
    cnpj_cpf: str = ""
    periodo_inicio: Optional[date] = None
    periodo_fim: Optional[date] = None
    valor_bruto: Decimal = field(default_factory=lambda: Decimal("0"))
    valor_liquido: Decimal = field(default_factory=lambda: Decimal("0"))
    valor_tarifa: Decimal = field(default_factory=lambda: Decimal("0"))


@dataclass
class CieloResult:
    success: bool = False
    resumo: CieloResumo = field(default_factory=CieloResumo)
    vendas: list[CieloVenda] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)


def parse_cielo_extrato(uploaded_file) -> CieloResult:
    try:
        from pypdf import PdfReader
    except ImportError:
        return CieloResult(success=False, erros=["pypdf não instalado."])

    try:
        raw = _read_file_bytes(uploaded_file)
        reader = PdfReader(io.BytesIO(raw))
    except Exception as exc:
        return CieloResult(success=False, erros=[f"Erro ao ler PDF: {exc}"])

    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    text = text.replace("\xa0", " ")
    if not text.strip():
        return CieloResult(success=False, erros=["Nenhum texto encontrado no PDF."])

    resumo = _parse_resumo(text)
    vendas = _parse_vendas(text)

    if not vendas:
        return CieloResult(success=False, erros=["Nenhuma venda Cielo encontrada no PDF."])

    return CieloResult(success=True, resumo=resumo, vendas=vendas)


def _parse_resumo(text: str) -> CieloResumo:
    resumo = CieloResumo()

    m = re.search(r"Usu[áa]rio:\s*(.+)", text, re.IGNORECASE)
    if m:
        resumo.razao_social = m.group(1).strip()

    m = re.search(r"Estabelecimento:\s*(\d+)", text, re.IGNORECASE)
    if m:
        resumo.codigo_estabelecimento = m.group(1).strip()

    m = re.search(r"CPF/CNPJ:\s*([\d./-]+)", text, re.IGNORECASE)
    if m:
        resumo.cnpj_cpf = m.group(1).strip()

    m = re.search(
        r"Data\s+da\s+venda\s+(\d{2}/\d{2}/\d{4})\s+[aà]\s+(\d{2}/\d{2}/\d{4})",
        text,
        re.IGNORECASE,
    )
    if m:
        resumo.periodo_inicio = _parse_date_br(m.group(1))
        resumo.periodo_fim = _parse_date_br(m.group(2))

    m = re.search(
        r"Quantidade\s+de\s+vendas\s+Valor\s+bruto\s+Taxa/tarifa\s+Valor\s+l[íi]quido\s+"
        r"(?P<qtd>\d+)\s+R\$\s*(?P<bruto>[\d.]+,\d{2})\s+-?R\$\s*(?P<tarifa>[\d.]+,\d{2})\s+R\$\s*(?P<liquido>[\d.]+,\d{2})",
        text,
        re.IGNORECASE,
    )
    if m:
        resumo.valor_bruto = _parse_brl_decimal(m.group("bruto"))
        resumo.valor_tarifa = abs(_parse_brl_decimal(m.group("tarifa")))
        resumo.valor_liquido = _parse_brl_decimal(m.group("liquido"))

    return resumo


def _parse_vendas(text: str) -> list[CieloVenda]:
    vendas_por_chave: dict[tuple[str, Optional[date], str], CieloVenda] = {}
    line_re = re.compile(
        r"^(?P<data>\d{2}/\d{2}/\d{4})\s+"
        r"(?P<hora>\d{2}:\d{2})\s+"
        r"(?P<codigo>\d{6,})\s+"
        r"(?P<cnpj>[\d./-]+)\s+"
        r"(?P<forma>.+?)\s+"
        r"(?P<bandeira>Visa|Mastercard|Elo|Hipercard|Amex|American Express)\s+"
        r"R\$\s*(?P<bruto>[\d.]+,\d{2})\s+"
        r"-?R\$\s*(?P<tarifa>[\d.]+,\d{2})\s+"
        r"R\$\s*(?P<liquido>[\d.]+,\d{2})\s+"
        r"(?P<status>Aprovada|Cancelada|Rejeitada)\s*$",
        re.IGNORECASE,
    )

    for raw_line in text.splitlines():
        line = " ".join(raw_line.strip().split())
        m = line_re.match(line)
        if not m:
            continue

        forma = m.group("forma").strip().upper()
        bandeira = m.group("bandeira").strip().upper()
        data_venda = _parse_date_br(m.group("data"))
        codigo_estabelecimento = m.group("codigo").strip()
        cartao = f"{bandeira} {forma}"
        chave = (cartao, data_venda, codigo_estabelecimento)
        venda = vendas_por_chave.get(chave)

        if not venda:
            venda = CieloVenda(
                cartao=cartao,
                data_venda=data_venda,
                codigo_estabelecimento=codigo_estabelecimento,
                quantidade=0,
            )
            vendas_por_chave[chave] = venda

        venda.quantidade += 1
        venda.valor_bruto += _parse_brl_decimal(m.group("bruto"))
        venda.valor_liquido += _parse_brl_decimal(m.group("liquido"))
        venda.valor_tarifa += abs(_parse_brl_decimal(m.group("tarifa")))

    return sorted(
        vendas_por_chave.values(),
        key=lambda venda: (venda.data_venda or date.max, venda.cartao, venda.codigo_estabelecimento),
    )
