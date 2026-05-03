"""
Parser de comprovantes bancários.

Tipos suportados:
  bb_boleto    — BB Comprovante de Pagamento de Títulos
  bb_pix       — BB Comprovante Pix
  bb_ted       — BB Comprovante de Transferência (TED / CC para CC)
  bb_convenio  — BB Comprovante de Pagamento (convênio: conta luz, tel, DAS...)
  bradesco_boleto — Bradesco NET EMPRESA Boleto de Cobrança
  darf         — Receita Federal Comprovante de Arrecadação (DARF)

Cada comprovante gera um ComprovanteResult com:
  documento    — número que bate com o campo 'documento' do extrato
  valor_total  — valor debitado/creditado no extrato
  itens        — lista de ItemComprovante (só itens com valor != 0)

Quando itens tem mais de 1 elemento, o XLS deve expandir o lançamento em
múltiplas linhas (uma por item), cuja soma = valor_total.
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
class ItemComprovante:
    descricao: str = ""
    valor: Decimal = field(default_factory=lambda: Decimal("0"))


@dataclass
class ComprovanteResult:
    success: bool = False
    tipo: str = ""
    pagina: int | None = None
    documento: str = ""          # normalizado (sem pontos/zeros à esquerda)
    data_pagamento: Optional[date] = None
    beneficiario: str = ""
    valor_documento: Decimal = field(default_factory=lambda: Decimal("0"))
    valor_total: Decimal = field(default_factory=lambda: Decimal("0"))
    tarifa_valor: Decimal = field(default_factory=lambda: Decimal("0"))
    juros_valor: Decimal = field(default_factory=lambda: Decimal("0"))
    multa_valor: Decimal = field(default_factory=lambda: Decimal("0"))
    desconto_valor: Decimal = field(default_factory=lambda: Decimal("0"))
    itens: list[ItemComprovante] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Ponto de entrada público
# ─────────────────────────────────────────────────────────────────────────────

def parse_comprovante_pdf(uploaded_file) -> list[ComprovanteResult]:
    """
    Parseia um PDF que pode conter múltiplos comprovantes concatenados
    (ex: BB exporta vários comprovantes num só PDF).

    Retorna lista de ComprovanteResult — um por comprovante encontrado.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return [ComprovanteResult(success=False, erros=["pypdf não instalado."])]

    try:
        raw = _read_file_bytes(uploaded_file)
        reader = PdfReader(io.BytesIO(raw))
    except Exception as exc:
        return [ComprovanteResult(success=False, erros=[f"Erro ao ler PDF: {exc}"])]

    results = []

    # Tenta página a página (BB exporta cada comprovante em 1-2 páginas)
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        r = _parse_single(text)
        if r.success:
            r.pagina = page_number
            results.append(r)

    # Se não encontrou nada página a página, tenta o documento inteiro
    if not results:
        full = "\n".join(page.extract_text() or "" for page in reader.pages)
        r = _parse_single(full)
        if r.success:
            r.pagina = 1 if len(reader.pages) == 1 else None
            results.append(r)

    return results or [ComprovanteResult(success=False, erros=["Formato de comprovante não reconhecido."])]


# ─────────────────────────────────────────────────────────────────────────────
# Detecção de tipo
# ─────────────────────────────────────────────────────────────────────────────

def _parse_single(text: str) -> ComprovanteResult:
    tl = text.lower()

    if "comprovante de arrecadação" in tl or "composição do documento de arrecadação" in tl:
        return _parse_darf(text)

    if ("comprovante de transação bancária" in tl or "comprovante de transacao bancaria" in tl) and "boleto de cobrança" in tl:
        return _parse_bradesco_boleto(text)

    if "comprovante de pagamento de titulos" in tl or "comprovante de pagamento de títulos" in tl:
        return _parse_bb_boleto(text)

    if "comprovante pix" in tl:
        return _parse_bb_pix(text)

    # TED ou transferência BB para BB
    if "comprovante de transferencia" in tl and ("ted" in tl or "conta corrente p/ conta corrente" in tl):
        return _parse_bb_ted(text)

    # Convênio (conta de consumo: luz, telefone, DAS, etc.)
    if "comprovante de pagamento" in tl and "convenio" in tl:
        return _parse_bb_convenio(text)

    return ComprovanteResult(success=False)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _brl(text: str) -> Decimal:
    return _parse_brl_decimal(text)


def _find(pattern: str, text: str, flags: int = re.IGNORECASE) -> str:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


def _clean_doc(raw: str) -> str:
    """Remove pontos, zeros à esquerda e espaços; retorna string limpa."""
    clean = raw.replace(".", "").replace(",", "").strip()
    return clean.lstrip("0") or clean   # preserva "0" puro se só zeros


# ─────────────────────────────────────────────────────────────────────────────
# Parser BB — Boleto de Títulos
# ─────────────────────────────────────────────────────────────────────────────

def _parse_bb_boleto(text: str) -> ComprovanteResult:
    r = ComprovanteResult(tipo="bb_boleto")

    doc_raw = _find(r"NR\.\s*DOCUMENTO\s+([\d.,]+)", text)
    r.documento = _clean_doc(doc_raw)

    data_str = _find(r"DATA DO PAGAMENTO\s+(\d{2}/\d{2}/\d{4})", text)
    r.data_pagamento = _parse_date_br(data_str) if data_str else None

    r.beneficiario = _find(r"NOME FANTASIA[:\s]+(.+?)[\n\r]", text)
    if not r.beneficiario:
        r.beneficiario = _find(r"BENEFICIARIO:\s*\n\s*(.+)", text)

    valor_doc = _brl(_find(r"VALOR DO DOCUMENTO\s+([\d.,]+)", text))
    valor_cobrado = _brl(_find(r"VALOR COBRADO\s+([\d.,]+)", text))
    juros = _brl(_find(r"JUROS\s+([\d.,]+)", text))
    multa = _brl(_find(r"MULTA\s+([\d.,]+)", text))
    desconto = _brl(_find(r"DESCONTO\s+([\d.,]+)", text))

    r.valor_documento = valor_doc
    r.valor_total = valor_cobrado if valor_cobrado > 0 else valor_doc

    # Só tem itens extras quando os valores diferem (desconto ou multa/juros)
    if valor_doc > 0 and valor_cobrado > 0 and valor_doc != valor_cobrado:
        r.itens.append(ItemComprovante(descricao="Principal", valor=valor_doc))
        if multa > 0:
            r.multa_valor = multa
            r.itens.append(ItemComprovante(descricao="Multa", valor=multa))
        if juros > 0:
            r.juros_valor = juros
            r.itens.append(ItemComprovante(descricao="Juros", valor=juros))
        if desconto > 0:
            r.desconto_valor = desconto
            r.itens.append(ItemComprovante(descricao="Desconto", valor=-desconto))

        if not any([multa > 0, juros > 0, desconto > 0]):
            diff = valor_cobrado - valor_doc
            if diff > 0:
                r.juros_valor = diff
                r.itens.append(ItemComprovante(descricao="Juros", valor=diff))
            else:
                r.desconto_valor = abs(diff)
                r.itens.append(ItemComprovante(descricao="Desconto", valor=diff))
    else:
        r.itens.append(ItemComprovante(descricao="Principal", valor=r.valor_total))

    r.success = r.valor_total > 0 and bool(r.documento)
    return r


# ─────────────────────────────────────────────────────────────────────────────
# Parser BB — Comprovante Pix
# ─────────────────────────────────────────────────────────────────────────────

def _parse_bb_pix(text: str) -> ComprovanteResult:
    r = ComprovanteResult(tipo="bb_pix")

    doc_raw = _find(r"DOCUMENTO[:\s]+([\d]+)", text)
    r.documento = _clean_doc(doc_raw)

    valor = _brl(_find(r"VALOR[:\s]+([\d.,]+)", text))
    tarifa = _brl(_find(r"TARIFA[:\s]+([\d.,]+)", text))

    data_str = _find(r"DATA[:\s]+(\d{2}/\d{2}/\d{4})", text)
    r.data_pagamento = _parse_date_br(data_str) if data_str else None

    r.beneficiario = _find(r"PAGO PARA[:\s]+(.+?)[\n\r]", text)

    r.valor_documento = valor
    r.valor_total = valor + tarifa
    r.tarifa_valor = tarifa

    r.itens.append(ItemComprovante(descricao="Principal", valor=valor))

    r.success = valor > 0 and bool(r.documento)
    return r


# ─────────────────────────────────────────────────────────────────────────────
# Parser BB — TED / Transferência CC→CC
# ─────────────────────────────────────────────────────────────────────────────

def _parse_bb_ted(text: str) -> ComprovanteResult:
    r = ComprovanteResult(tipo="bb_ted")

    # TED: "DOCUMENTO: 010201" / Transferência: "NR. DOCUMENTO  610.577.000.025.632"
    doc_raw = _find(r"(?:NR\.\s*)?DOCUMENTO[:\s]+([\d.]+)", text)
    r.documento = _clean_doc(doc_raw)

    valor = _brl(_find(r"VALOR(?:\s+TOTAL)?[:\s]+([\d.,]+)", text))
    tarifa = _brl(_find(r"TARIFA[:\s]+([\d.,]+)", text))

    data_str = _find(r"DATA DA TRANSFERENCIA\s+(\d{2}/\d{2}/\d{4})", text)
    if not data_str:
        data_str = _find(r"DEBITO EM[:\s]+(\d{2}/\d{2}/\d{4})", text)
    r.data_pagamento = _parse_date_br(data_str) if data_str else None

    r.beneficiario = _find(r"FAVORECIDO[:\s]+(.+?)[\n\r]", text)
    if not r.beneficiario:
        r.beneficiario = _find(r"CLIENTE:\s*(.+?)[\n\r]", text)

    r.valor_documento = valor
    r.valor_total = valor + tarifa
    r.tarifa_valor = tarifa
    r.itens.append(ItemComprovante(descricao="Principal", valor=valor))

    r.success = valor > 0 and bool(r.documento)
    return r


# ─────────────────────────────────────────────────────────────────────────────
# Parser BB — Convênio (conta de consumo / DAS)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_bb_convenio(text: str) -> ComprovanteResult:
    r = ComprovanteResult(tipo="bb_convenio")

    doc_raw = _find(r"DOCUMENTO[:\s]+([\d]+)", text)
    r.documento = _clean_doc(doc_raw)

    valor = _brl(_find(r"Valor Total\s+([\d.,]+)", text))

    data_str = _find(r"Data do pagamento\s+(\d{2}/\d{2}/\d{4})", text)
    r.data_pagamento = _parse_date_br(data_str) if data_str else None

    r.beneficiario = _find(r"Convenio\s+(.+?)[\n\r]", text)

    r.valor_documento = valor
    r.valor_total = valor
    r.itens.append(ItemComprovante(descricao="Principal", valor=valor))

    r.success = valor > 0 and bool(r.documento)
    return r


# ─────────────────────────────────────────────────────────────────────────────
# Parser Bradesco — Boleto de Cobrança (NET EMPRESA)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_bradesco_boleto(text: str) -> ComprovanteResult:
    r = ComprovanteResult(tipo="bradesco_boleto")

    # "N° de controle: 585.772... | Documento: 0004604"
    doc_raw = _find(r"Documento[:\s]+([\d]+)", text)
    r.documento = _clean_doc(doc_raw)

    data_str = _find(r"Data da operação[:\s]+(\d{2}/\d{2}/\d{4})", text)
    if not data_str:
        data_str = _find(r"Data de débito[:\s]+(\d{2}/\d{2}/\d{4})", text)
    r.data_pagamento = _parse_date_br(data_str) if data_str else None

    r.beneficiario = _find(r"Descrição[:\s]+(.+?)[\n\r]", text)
    if not r.beneficiario:
        r.beneficiario = _find(r"Nome Fantasia\s*Beneficiário[:\s]+(.+)", text)

    valor_total = _brl(_find(r"Valor total[:\s]+R\$\s*([\d.,]+)", text))
    juros      = _brl(_find(r"Juros[:\s]+R\$\s*([\d.,]+)", text))
    multa      = _brl(_find(r"Multa[:\s]+R\$\s*([\d.,]+)", text))
    desconto   = _brl(_find(r"Desconto[:\s]+R\$\s*([\d.,]+)", text))
    abatimento = _brl(_find(r"Abatimento[:\s]+R\$\s*([\d.,]+)", text))

    r.valor_total = valor_total
    r.juros_valor = juros
    r.multa_valor = multa
    r.desconto_valor = desconto + abatimento

    # Principal = valor pago - juros - multa + desconto + abatimento
    principal = valor_total - juros - multa + desconto + abatimento
    r.valor_documento = principal

    r.itens.append(ItemComprovante(descricao="Principal", valor=principal))
    if multa > 0:
        r.itens.append(ItemComprovante(descricao="Multa", valor=multa))
    if juros > 0:
        r.itens.append(ItemComprovante(descricao="Juros", valor=juros))
    if desconto > 0:
        r.itens.append(ItemComprovante(descricao="Desconto", valor=-desconto))
    if abatimento > 0:
        r.itens.append(ItemComprovante(descricao="Abatimento", valor=-abatimento))

    r.success = valor_total > 0 and bool(r.documento)
    return r


# ─────────────────────────────────────────────────────────────────────────────
# Parser DARF — Receita Federal
# ─────────────────────────────────────────────────────────────────────────────

_DARF_ITEM_RE = re.compile(
    r"^\s*(\d{4})\s+(.+?)\s+([\d.,]+)\s+[\d.,\-]+\s+[\d.,\-]+\s+([\d.,]+)\s*$",
    re.MULTILINE,
)

def _parse_darf(text: str) -> ComprovanteResult:
    r = ComprovanteResult(tipo="darf")

    # Número do documento (longo, ex: 07162336355012309)
    r.documento = _find(r"Número do Documento\s*[\n\r]+\s*(\S+)", text)
    if not r.documento:
        r.documento = _find(r"\b(\d{17,18})\b", text)

    # Data de arrecadação (na parte inferior do comprovante)
    data_str = _find(r"Data de Arrecadação\s*[\n\r]+\s*(\d{2}/\d{2}/\d{4})", text)
    if not data_str:
        # Fallback: "Documento pago via PIX  10/04/2024"
        data_str = _find(r"(?:pago via PIX|Arrecadação)[^\d]*(\d{2}/\d{2}/\d{4})", text)
    r.data_pagamento = _parse_date_br(data_str) if data_str else None

    r.beneficiario = "RECEITA FEDERAL"

    # Itens — cada linha de código tributário
    total = Decimal("0")
    for m in _DARF_ITEM_RE.finditer(text):
        codigo = m.group(1)
        desc_raw = m.group(2).strip()
        valor_principal = _brl(m.group(3))
        if valor_principal <= 0:
            continue
        # Descrição resumida + código
        desc_resumida = re.sub(r"\s{2,}", " ", desc_raw)[:45]
        r.itens.append(ItemComprovante(descricao=f"{desc_resumida} ({codigo})", valor=valor_principal))
        total += valor_principal

    # Total geral
    totais_m = re.search(
        r"Totais\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)",
        text, re.IGNORECASE,
    )
    r.valor_total = _brl(totais_m.group(1)) if totais_m else total
    r.valor_documento = total

    r.success = bool(r.itens) and r.valor_total > 0
    return r
