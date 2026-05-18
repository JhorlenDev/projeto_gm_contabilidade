"""
Parser para Nota Fiscal de Serviços Eletrônica (NFS-e) emitida por prefeitura.

Utiliza pdfplumber e pandas para extrair e organizar os dados.
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
# Parser Principal
# ─────────────────────────────────────────────────────────────────────────────

def parse_nfse_prefeitura(uploaded_file) -> NfseResult:
    if not pdfplumber:
        return NfseResult(success=False, erros=["pdfplumber não está instalado. Rode: pip install pdfplumber pandas"])

    try:
        raw = _read_file_bytes(uploaded_file)
        pdf_file = io.BytesIO(raw)
    except Exception as exc:
        return NfseResult(success=False, erros=[f"Erro ao ler PDF: {exc}"])

    dados_extraidos = []

    try:
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                text = page.extract_text(layout=False) or ""
                if not text.strip():
                    continue
                
                # Extração via Regex do Texto Completo da Página
                numero_nota_m = re.search(r"N[uú]mero da NFS-e[\s\n]*(\d+)", text, re.IGNORECASE)
                numero_nota = numero_nota_m.group(1) if numero_nota_m else None
                
                if not numero_nota:
                    continue  # Pula se não for uma nota fiscal
                
                data_emissao_m = re.search(r"Emiss[aã]o da NFS-e[\s\n]*(\d{2}/\d{2}/\d{4})", text, re.IGNORECASE)
                data_emissao = data_emissao_m.group(1) if data_emissao_m else None
                
                tomador_block = _extract_tomador_block(text)
                cpf_tomador, nome_tomador = _extract_tomador_identificacao(tomador_block)
                
                valor_m = re.search(r"(?:Valor Total dos Servi[çc]os|Valor L[íi]quido da NFS-e:|Total dos Servi[çc]os)\s*R?\$?\s*([\d\.,]+)", text, re.IGNORECASE)
                valor = valor_m.group(1) if valor_m else None
                
                vencimento_m = re.search(r"Venc:\s*(\d{2}/\d{2}/\d{4})", text, re.IGNORECASE)
                data_vencimento = vencimento_m.group(1) if vencimento_m else None
                
                tipo_pagamento_m = re.search(r"FATURAS?:\s*([^V]+)", text, re.IGNORECASE)
                tipo_pagamento = tipo_pagamento_m.group(1).strip() if tipo_pagamento_m else None

                dados_extraidos.append({
                    "numero_nota": numero_nota,
                    "data_emissao": data_emissao,
                    "data_pagamento": data_vencimento,
                    "nome_tomador": nome_tomador,
                    "cpf_tomador": cpf_tomador,
                    "valor": valor,
                    "tipo_pagamento": tipo_pagamento
                })
                
    except Exception as exc:
        return NfseResult(success=False, erros=[f"Erro ao processar paginas com pdfplumber: {exc}"])

    if not dados_extraidos:
        return NfseResult(success=False, erros=["Nenhuma NFS-e válida encontrada no PDF."])

    # ─────────────────────────────────────────────────────────────────────────
    # Organização e Limpeza via Pandas
    # ─────────────────────────────────────────────────────────────────────────
    df = pd.DataFrame(dados_extraidos)

    # Limpeza e Padronização
    df['numero_nota'] = df['numero_nota'].fillna("").astype(str)
    df['nome_tomador'] = df['nome_tomador'].fillna("").astype(str)
    df['cpf_tomador'] = df['cpf_tomador'].fillna("").astype(str)
    
    # Conversão de Datas
    df['data_emissao_obj'] = df['data_emissao'].apply(_parse_date_br)
    df['data_pagamento_obj'] = df['data_pagamento'].apply(_parse_date_br)
    
    # Valores Monetários
    df['valor_dec'] = df['valor'].apply(_parse_brl_decimal)
    
    # Tipo Pagamento
    df['tipo_pagamento'] = df['tipo_pagamento'].fillna("").apply(_normalizar_tipo)

    # ─────────────────────────────────────────────────────────────────────────
    # Conversão do DataFrame para dataclasses
    # ─────────────────────────────────────────────────────────────────────────
    notas = []
    for _, row in df.iterrows():
        notas.append(NfseItem(
            numero_nota=row['numero_nota'],
            data_emissao=row['data_emissao_obj'],
            data_pagamento=row['data_pagamento_obj'],
            nome_tomador=row['nome_tomador'],
            cpf_tomador=row['cpf_tomador'],
            valor=row['valor_dec'],
            tipo_pagamento=row['tipo_pagamento']
        ))

    return NfseResult(success=True, notas=notas)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_tomador_block(text: str) -> str:
    start = text.find("TOMADOR DE SERVIÇOS")
    if start < 0:
        start = text.find("TOMADOR DE SERVICOS")
    if start < 0:
        return text

    end_candidates = [
        text.find("Discriminação dos Serviços", start),
        text.find("Discriminacao dos Servicos", start),
        text.find("Imposto Sobre Serviços", start),
    ]
    end_candidates = [pos for pos in end_candidates if pos > start]
    end = min(end_candidates) if end_candidates else len(text)
    return text[start:end]


def _extract_tomador_identificacao(tomador_block: str) -> tuple[str | None, str | None]:
    doc_re = r"(\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})"
    stop_words = (
        "LOGRADOURO",
        "COMPLEMENTO",
        "BAIRRO",
        "CEP",
        "CIDADE",
        "TELEFONE",
        "E-MAIL",
        "EMAIL",
    )

    lines = [line.strip() for line in tomador_block.splitlines() if line.strip()]
    for line in lines:
        doc_m = re.search(doc_re, line)
        if not doc_m:
            continue

        cpf_tomador = doc_m.group(1)
        nome = line[doc_m.end():].strip(" :-")
        if nome and not any(nome.upper().startswith(word) for word in stop_words):
            return cpf_tomador, nome
        return cpf_tomador, None

    return None, None


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
