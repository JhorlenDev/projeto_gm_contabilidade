"""
Pacote de parsers de extrato bancário.

Estrutura:
  base.py      — helpers, dataclasses e parser genérico (fallback)
  bradesco.py  — BradescoExtratoParser
  amazonia.py  — AmazoniaExtratoParser  (Banco da Amazônia / BASA)
  bb.py        — BancoBrasilExtratoParser
  santander.py — SantanderExtratoParser

Uso:
  from services.parsers import process_extrato_pdf
  from services.parsers import SantanderExtratoParser
"""
from __future__ import annotations

import io

from .base import (
    ExtratoHeader,
    ExtratoResult,
    LancamentoExtrato,
    PDFExtratoParser,
    _parse_brl_decimal,
    _parse_date_br,
    _read_file_bytes,
)
from .bradesco import BradescoExtratoParser
from .amazonia import AmazoniaExtratoParser
from .bb import BancoBrasilExtratoParser
from .santander import SantanderExtratoParser

__all__ = [
    "ExtratoHeader",
    "ExtratoResult",
    "LancamentoExtrato",
    "PDFExtratoParser",
    "BradescoExtratoParser",
    "AmazoniaExtratoParser",
    "BancoBrasilExtratoParser",
    "SantanderExtratoParser",
    "process_extrato_pdf",
    "_parse_brl_decimal",
    "_parse_date_br",
    "_read_file_bytes",
]


def process_extrato_pdf(uploaded_file, banco: str = "auto") -> ExtratoResult:
    """
    Processa um extrato bancário em PDF.
    Detecta automaticamente o banco pelo conteúdo ou usa o parser indicado.
    Lê os bytes uma única vez para evitar I/O operation on closed file.

    Bancos suportados: bradesco, amazonia, bb, santander, generic (fallback).
    """
    try:
        raw: bytes = _read_file_bytes(uploaded_file)
    except Exception as exc:
        return ExtratoResult(success=False, erros=[f"Erro ao ler arquivo: {exc}"])

    if banco == "auto":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            first_page = reader.pages[0].extract_text() or "" if reader.pages else ""
            fp_lower = first_page.lower()
            if "bradesco" in fp_lower or "net empresa" in fp_lower:
                banco = "bradesco"
            elif "banco da amazônia" in fp_lower or "banco da amazonia" in fp_lower or "gesop" in fp_lower or "pd_ccor" in fp_lower or "basa" in fp_lower:
                banco = "amazonia"
            elif "santander" in fp_lower or "extrato consolidado inteligente" in fp_lower or "contamax" in fp_lower:
                banco = "santander"
            elif "banco do brasil" in fp_lower or "bb rende" in fp_lower or "bb seguro" in fp_lower or "consultas - extrato de conta corrente" in fp_lower:
                banco = "bb"
            else:
                banco = "generic"
        except Exception:
            banco = "generic"

    class _BytesFile:
        """Adapta bytes para a interface de uploaded_file esperada pelos parsers."""
        def __init__(self, data: bytes):
            self._data = data
        def open(self, _mode="rb"):
            pass
        def read(self) -> bytes:
            return self._data
        def close(self):
            pass

    wrapped = _BytesFile(raw)

    if banco == "bradesco":
        return BradescoExtratoParser().parse(wrapped)
    if banco == "amazonia":
        return AmazoniaExtratoParser().parse(wrapped)
    if banco == "bb":
        return BancoBrasilExtratoParser().parse(wrapped)
    if banco == "santander":
        return SantanderExtratoParser().parse(wrapped)

    return PDFExtratoParser().parse(wrapped)
