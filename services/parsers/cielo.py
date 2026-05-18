"""
Parser para extrato Cielo "Detalhado de vendas Cielo".
Utiliza pdfplumber e pandas para extrair e organizar os dados em lote.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional
import pandas as pd

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

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
    if not pdfplumber:
        return CieloResult(success=False, erros=["pdfplumber não instalado."])

    try:
        raw = _read_file_bytes(uploaded_file)
        pdf_file = io.BytesIO(raw)
    except Exception as exc:
        return CieloResult(success=False, erros=[f"Erro ao ler PDF: {exc}"])

    try:
        pages = []
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                text = page.extract_text(layout=False) or ""
                pages.append(text.replace("\xa0", " "))
    except Exception as exc:
        return CieloResult(success=False, erros=[f"Erro no pdfplumber: {exc}"])

    full_text = "\n".join(pages)

    if not full_text.strip():
        return CieloResult(success=False, erros=["Nenhum texto encontrado no PDF."])

    resumo = _parse_resumo(full_text)
    dados_brutos = _extract_raw_data(full_text)

    if not dados_brutos:
        return CieloResult(success=False, erros=["Nenhuma venda Cielo encontrada no PDF."])

    # Utilizando Pandas para agrupar e somar os valores das transações detalhadas
    df = pd.DataFrame(dados_brutos)

    # Conversão e higienização
    df['data_venda_obj'] = df['data'].apply(_parse_date_br)
    df['cartao'] = (df['bandeira'].str.upper() + " " + df['forma'].str.upper()).str.strip()
    df['codigo_estabelecimento'] = df['codigo'].str.strip()
    
    df['valor_bruto_dec'] = df['bruto'].apply(_parse_brl_decimal)
    df['valor_tarifa_dec'] = df['tarifa'].apply(_parse_brl_decimal).abs()
    df['valor_liquido_dec'] = df['liquido'].apply(_parse_brl_decimal)

    # A Cielo lista transações individuais (Detalhado), mas o motor precisa delas consolidadas por dia/cartão
    df_consolidado = df.groupby(['cartao', 'data_venda_obj', 'codigo_estabelecimento']).agg({
        'data': 'count', # count = quantidade de vendas
        'valor_bruto_dec': 'sum',
        'valor_tarifa_dec': 'sum',
        'valor_liquido_dec': 'sum'
    }).reset_index().rename(columns={'data': 'quantidade'})

    # Ordena cronologicamente
    df_consolidado = df_consolidado.sort_values(by=['data_venda_obj', 'cartao', 'codigo_estabelecimento'])

    vendas = []
    for _, row in df_consolidado.iterrows():
        vendas.append(CieloVenda(
            cartao=row['cartao'],
            data_venda=row['data_venda_obj'],
            codigo_estabelecimento=row['codigo_estabelecimento'],
            quantidade=row['quantidade'],
            valor_bruto=row['valor_bruto_dec'],
            valor_liquido=row['valor_liquido_dec'],
            valor_tarifa=row['valor_tarifa_dec']
        ))

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


def _extract_raw_data(text: str) -> list[dict]:
    dados = []
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

        dados.append(m.groupdict())

    return dados
