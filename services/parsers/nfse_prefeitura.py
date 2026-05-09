"""
Parser para Nota Fiscal de Serviços Eletrônica (NFS-e) emitida por prefeitura.

Extrai por página do PDF:
  - Número da NFS-e
  - Data de emissão
  - Data de pagamento (campo "Venc:" nas Informações Complementares)
  - Nome do tomador
  - CPF/CNPJ do tomador
  - Valor total dos serviços
  - Tipo de pagamento (PIX, CARTÃO DE DÉBITO, CARTÃO DE CRÉDITO, DEPÓSITO,
                       PAGAMENTO À VISTA — vazio ou ausente = DINHEIRO)
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from .base import _parse_brl_decimal, _parse_date_br, _read_file_bytes


# ─────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NfseItem:
    numero_nota: str = ""
    data_emissao: Optional[date] = None
    data_pagamento: Optional[date] = None
    nome_tomador: str = ""
    cpf_tomador: str = ""
    valor: Decimal = field(default_factory=lambda: Decimal("0"))
    tipo_pagamento: str = ""


@dataclass
class NfseResult:
    success: bool = False
    notas: list[NfseItem] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Ponto de entrada público
# ─────────────────────────────────────────────────────────────────────────────

def parse_nfse_prefeitura(uploaded_file) -> NfseResult:
    """
    Parseia um PDF de NFS-e da prefeitura (pode conter múltiplas notas/páginas).
    Retorna NfseResult com lista de NfseItem — uma por nota encontrada.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return NfseResult(success=False, erros=["pypdf não instalado."])

    try:
        raw = _read_file_bytes(uploaded_file)
        reader = PdfReader(io.BytesIO(raw))
    except Exception as exc:
        return NfseResult(success=False, erros=[f"Erro ao ler PDF: {exc}"])

    notas: list[NfseItem] = []

    for page in reader.pages:
        # Tenta extração padrão e extração com layout (pypdf >= 3.x)
        text = page.extract_text() or ""
        if not text.strip():
            try:
                from pypdf import PageObject
                text = page.extract_text(extraction_mode="layout") or ""
            except Exception:
                pass

        if not text.strip():
            continue

        item = _parse_page(text)
        if item:
            notas.append(item)

    if not notas:
        return NfseResult(success=False, erros=["Nenhuma NFS-e encontrada no PDF."])

    return NfseResult(success=True, notas=notas)


# ─────────────────────────────────────────────────────────────────────────────
# Parser de página individual
# ─────────────────────────────────────────────────────────────────────────────

def _parse_page(text: str) -> Optional[NfseItem]:
    item = NfseItem()

    # ── Número da NFS-e ──────────────────────────────────────────────────────
    # O pypdf extrai as colunas intercaladas, então o número pode vir na
    # próxima linha mas após conteúdo de outra coluna.
    # Ex: "Numero da NFS-e\nPREF. MUNIC. DE TEFE - AM 512932"
    for pat in [
        r"N[uú]mero da NFS-e\s+(\d{4,10})\b",                    # mesmo linha/whitespace direto
        r"N[uú]mero da NFS-e\s*\n[^\n]*?(\d{5,10})\b",           # número na linha seguinte (com lixo de outra coluna)
        r"N[uú]mero da NFS-e[\s\S]{0,80}?(\b\d{5,10}\b)",        # até 80 chars depois
        r"Chave de Acesso\s*\n[^\n]*?(\d{5,10})\b",
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            item.numero_nota = m.group(1).strip()
            break

    # Se não achou número de NFS-e, não é uma NFS-e válida
    if not item.numero_nota:
        return None

    # ── Data de emissão ──────────────────────────────────────────────────────
    for pat in [
        r"Data e Hora de Emiss[aã]o da NFS-e\s+(\d{2}/\d{2}/\d{4})",
        r"Data e Hora de Emiss[aã]o da NFS-e\s*\n[^\n]*?(\d{2}/\d{2}/\d{4})",
        r"Data e Hora de Emiss[aã]o[\s\S]{0,80}?(\d{2}/\d{2}/\d{4})",
        # fallback: data que aparece junto com horário " às HH:MM" — é sempre a emissão
        r"(\d{2}/\d{2}/\d{4})\s+[àas]+\s+\d{2}:\d{2}",
        r"(\d{2}/\d{2}/\d{4})\s+\d{2}:\d{2}:\d{2}",
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            item.data_emissao = _parse_date_br(m.group(1))
            if item.data_emissao:
                break
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            item.data_emissao = _parse_date_br(m.group(1))
            break

    # ── Tomador: CPF/CNPJ e Nome ─────────────────────────────────────────────
    # O pypdf extrai os dados do TOMADOR ANTES do header "TOMADOR DE SERVIÇOS".
    # Estrutura real extraída:
    #   Nome/Razão Social\n{NOME_TOMADOR}\n...CPF/CNPJ/Documento\n{CPF}\n
    #   TOMADOR DE SERVIÇOS
    #   ...
    #   {NOME_PRESTADOR}\nNome/Razão Social\n{CNPJ_PRESTADOR}
    #   PRESTADOR DE SERVIÇOS

    # Nome do tomador: primeira ocorrência de "Nome/Razão Social\n" — linha seguinte é o nome
    nome_m = re.search(r"Nome/Raz[aã]o Social\s*\n([A-ZÁÉÍÓÚÂÊÎÔÛÃÕÇÀÜ][^\n]+)", text, re.IGNORECASE)
    if nome_m:
        item.nome_tomador = nome_m.group(1).strip()

    # CPF do tomador: label "CPF/CNPJ/Documento" é exclusivo do tomador
    # (prestador usa apenas "CPF/CNPJ" sem "/Documento")
    cpf_m = re.search(
        r"CPF/CNPJ/Documento[^\n]*\n(\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})",
        text,
    )
    if not cpf_m:
        # Fallback: CPF isolado numa linha (formato XXX.XXX.XXX-XX)
        cpf_m = re.search(r"\n(\d{3}\.\d{3}\.\d{3}-\d{2})\n", text)
    if cpf_m:
        item.cpf_tomador = cpf_m.group(1)

    # ── Valor total dos serviços ─────────────────────────────────────────────
    for pat in [
        r"Valor Total dos Servi[çc]os\s+R?\$?\s*([\d\.]+,\d{2})",
        r"Valor L[íi]quido da NFS-e:\s*R?\$?\s*([\d\.]+,\d{2})",
        r"Total dos Servi[çc]os\s+R?\$?\s*([\d\.]+,\d{2})",
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            item.valor = _parse_brl_decimal(m.group(1))
            if item.valor > 0:
                break

    # ── Informações complementares → tipo de pagamento e data de pagamento ───
    # Padrão: "FATURAS: TIPO Venc: DD/MM/YYYY R$ X Doc: Y Obs: Z"
    comp_m = re.search(
        r"FATURAS?:\s*(.+?)(?:Obs:|$)",
        text, re.IGNORECASE | re.DOTALL,
    )
    if comp_m:
        fatura_line = comp_m.group(1).replace("\n", " ").strip()

        # Data de pagamento (vencimento)
        venc_m = re.search(r"Venc:\s*(\d{2}/\d{2}/\d{4})", fatura_line, re.IGNORECASE)
        if venc_m:
            item.data_pagamento = _parse_date_br(venc_m.group(1))

        # Tipo: tudo antes do "Venc:" ou do valor "R$"
        tipo_raw = re.sub(r"\s*Venc:.*", "", fatura_line, flags=re.IGNORECASE)
        tipo_raw = re.sub(r"\s*R\$.*", "", tipo_raw, flags=re.IGNORECASE).strip()
        item.tipo_pagamento = _normalizar_tipo(tipo_raw)
    else:
        item.tipo_pagamento = "DINHEIRO"

    return item


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_tomador_block(text: str) -> str:
    """Extrai o bloco referente ao TOMADOR DE SERVIÇOS."""
    m = re.search(
        r"TOMADOR DE SERVI[ÇC]OS(.+?)(?:Discrimina[çc][aã]o dos Servi[çc]os|NFS-e COMPOSTA|$)",
        text, re.IGNORECASE | re.DOTALL,
    )
    return m.group(1) if m else ""


_TIPO_MAP: dict[str, str] = {
    "PIX": "PIX",
    "CARTÃO DE DÉBITO": "CARTÃO DE DÉBITO",
    "CARTAO DE DEBITO": "CARTÃO DE DÉBITO",
    "CARTÃO DE CRÉDITO": "CARTÃO DE CRÉDITO",
    "CARTAO DE CREDITO": "CARTÃO DE CRÉDITO",
    "DEPÓSITO": "DEPÓSITO",
    "DEPOSITO": "DEPÓSITO",
    "PAGAMENTO À VISTA": "PAGAMENTO À VISTA",
    "PAGAMENTO A VISTA": "PAGAMENTO À VISTA",
    "DINHEIRO": "DINHEIRO",
    "": "DINHEIRO",
}


def _normalizar_tipo(raw: str) -> str:
    raw_upper = raw.upper().strip()
    if raw_upper in _TIPO_MAP:
        return _TIPO_MAP[raw_upper]
    for key, val in _TIPO_MAP.items():
        if key and key in raw_upper:
            return val
    return raw_upper if raw_upper else "DINHEIRO"
