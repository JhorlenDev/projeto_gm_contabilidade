"""
Parser para extrato Getnet "O que vendi - Consolidado por Data de Vendas".
Utiliza pdfplumber e pandas para extrair e organizar os dados em tabelas.
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
    if not pdfplumber:
        return GetnetResult(success=False, erros=["pdfplumber não instalado."])

    try:
        raw = _read_file_bytes(uploaded_file)
        pdf_file = io.BytesIO(raw)
    except Exception as exc:
        return GetnetResult(success=False, erros=[f"Erro ao ler PDF: {exc}"])

    try:
        with pdfplumber.open(pdf_file) as pdf:
            # 1) Extrair resumo (usando a primeira página em formato texto)
            first_page_text = pdf.pages[0].extract_text(layout=False) or ""
            resumo = _parse_resumo(first_page_text)

            # 2) Extrair vendas via tabelas nativas do pdfplumber
            todas_linhas_tabela = []
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        # Heurística para achar a linha de transação:
                        # O índice 0 deve ter algo numérico (cod estabelecimento), e tamanho suficiente
                        if len(row) > 11 and row[0] and str(row[0]).strip().isdigit():
                            todas_linhas_tabela.append(row)

    except Exception as exc:
        return GetnetResult(success=False, erros=[f"Erro ao processar PDF com pdfplumber: {exc}"])

    if not todas_linhas_tabela:
        return GetnetResult(success=False, erros=["Nenhuma venda Getnet encontrada no PDF."])

    # 3) Usar Pandas para organizar os dados tabulares
    df = pd.DataFrame(todas_linhas_tabela)
    
    # As colunas esperadas baseadas no layout da Getnet:
    # 0: Cód Estabelecimento | 1: Cartões | 3: Data Venda | 4: Qtd Vendas | 6: Valor Bruto | 8: Valor Tarifa | 11: Valor Líquido
    
    df['cartao'] = df[1].fillna("").astype(str).str.strip()
    df['data_venda_obj'] = df[3].apply(_parse_date_br)
    df['codigo_estabelecimento'] = df[0].fillna("").astype(str).str.strip()
    df['quantidade'] = pd.to_numeric(df[4].fillna("0").astype(str).str.replace(r'\D', '', regex=True), errors='coerce').fillna(0).astype(int)
    
    # Valores financeiros
    df['valor_bruto_dec'] = df[6].apply(_parse_brl_decimal)
    df['valor_tarifa_dec'] = df[8].apply(_parse_brl_decimal)
    df['valor_liquido_dec'] = df[11].apply(_parse_brl_decimal)

    vendas = []
    for _, row in df.iterrows():
        vendas.append(GetnetVenda(
            cartao=row['cartao'],
            data_venda=row['data_venda_obj'],
            codigo_estabelecimento=row['codigo_estabelecimento'],
            quantidade=row['quantidade'],
            valor_bruto=row['valor_bruto_dec'],
            valor_liquido=row['valor_liquido_dec'],
            valor_tarifa=row['valor_tarifa_dec']
        ))

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
