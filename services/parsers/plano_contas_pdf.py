"""
Parser de PDF de Plano de Contas.

Extrai as contas a partir de um PDF no formato exportado pelo sistema contábil,
com colunas: Código | Classificação | Nome | Tipo | DRE
"""
from __future__ import annotations

import re
from io import BytesIO
from typing import Generator

from pypdf import PdfReader

# Tipos conhecidos — ordenados do mais longo para o mais curto para evitar match parcial
_TIPOS = [
    "Lucro/Prejuizo Acumulado",
    "Reserva Lucro a Realizar",
    "Reserva Estatutaria",
    "Reserva Legal",
    "Agio Ações",
    "Sintética",
    "Analitica",
    "Imobilizado",
    "Fornecedor",
    "Cliente",
    "Capital",
    "Receita",
    "Despesa",
    "Custo",
    "Banco",
    "Caixa",
]

_TIPO_RE = re.compile(
    r"(" + "|".join(re.escape(t) for t in _TIPOS) + r")"
)

# Linhas a ignorar (cabeçalhos de página e de coluna)
_SKIP_RE = re.compile(
    r"^(Planos?\s+Cta\s+Empresa|Código\s+Classificação|Código|Classificação|Nome\s*$|Tipo\s*$|DRE\s*$)$",
    re.IGNORECASE,
)

# Linha que inicia uma nova conta: começa com número(s) seguido de espaço e depois dígito ou ponto
_CONTA_INICIO_RE = re.compile(r"^(\d+)\s+([\d.])")

# Classificação composta só de dígitos e pontos
_CLASSIF_ONLY_RE = re.compile(r"^[\d.]+$")

# Apenas dígitos (continuação de classificação que quebrou de linha)
_DIGITS_ONLY_RE = re.compile(r"^\d+$")


def _extract_lines(file_bytes: bytes) -> list[str]:
    """Extrai todas as linhas não-vazias e não-cabeçalho do PDF."""
    reader = PdfReader(BytesIO(file_bytes))
    lines: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if _SKIP_RE.match(line):
                continue
            lines.append(line)
    return lines


def _group_blocks(lines: list[str]) -> Generator[list[str], None, None]:
    """Agrupa linhas em blocos, onde cada bloco representa uma conta."""
    current: list[str] = []
    for line in lines:
        if _CONTA_INICIO_RE.match(line):
            if current:
                yield current
            current = [line]
        else:
            if current:
                current.append(line)
    if current:
        yield current


def _parse_block(block: list[str]) -> dict | None:
    """Converte um bloco de linhas em um dict com os campos da conta."""
    first = block[0]
    m = re.match(r"^(\d+)\s+([\d.]+)(.*?)$", first)
    if not m:
        return None

    codigo = m.group(1)
    classif = m.group(2)
    rest_of_first = m.group(3).strip()

    nome_parts: list[str] = []
    if rest_of_first:
        nome_parts.append(rest_of_first)

    for line in block[1:]:
        # Continuação da classificação que quebrou de linha (ex: "1.10.20.10.30." + "001")
        if classif.endswith(".") and _DIGITS_ONLY_RE.match(line):
            classif += line
        # Linha só com dígitos/pontos colada ao código anterior (ex: "001NOME...")
        elif _CLASSIF_ONLY_RE.match(line) and not nome_parts:
            classif += line
        else:
            nome_parts.append(line)

    full_text = " ".join(nome_parts)

    # Encontra o tipo (de trás pra frente — pega o último match para evitar falso positivo em nomes)
    matches = list(_TIPO_RE.finditer(full_text))
    if not matches:
        return None

    # Usa o último match como tipo principal
    m_tipo = matches[-1]
    tipo = m_tipo.group(1)
    nome = full_text[: m_tipo.start()].strip()

    # Limpa espaços extras no nome
    nome = re.sub(r"\s+", " ", nome).strip()

    return {
        "codigo": codigo,
        "classificacao": classif,
        "nome": nome,
        "tipo": tipo,
    }


def parse_plano_contas_pdf(file_bytes: bytes) -> list[dict]:
    """
    Recebe os bytes de um PDF de Plano de Contas e retorna uma lista de dicts:
      [{ "codigo": "98", "classificacao": "1.10.10.20.01", "nome": "Banco da Amazônia S.A.", "tipo": "Banco" }, ...]
    """
    lines = _extract_lines(file_bytes)
    accounts: list[dict] = []
    for block in _group_blocks(lines):
        account = _parse_block(block)
        if account:
            accounts.append(account)
    return accounts
