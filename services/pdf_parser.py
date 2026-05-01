"""
Módulo de compatibilidade — mantido para não quebrar imports existentes.
A implementação foi movida para services/parsers/.

  services/parsers/base.py      — helpers, dataclasses e parser genérico
  services/parsers/bradesco.py  — BradescoExtratoParser
  services/parsers/amazonia.py  — AmazoniaExtratoParser (Banco da Amazônia / BASA)
  services/parsers/bb.py        — BancoBrasilExtratoParser
  services/parsers/santander.py — SantanderExtratoParser
"""
from services.parsers import (  # noqa: F401
    ExtratoHeader,
    ExtratoResult,
    LancamentoExtrato,
    PDFExtratoParser,
    BradescoExtratoParser,
    AmazoniaExtratoParser,
    BancoBrasilExtratoParser,
    SantanderExtratoParser,
    process_extrato_pdf,
    _parse_brl_decimal,
    _parse_date_br,
    _read_file_bytes,
)
