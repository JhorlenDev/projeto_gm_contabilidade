from __future__ import annotations

import csv
import io
import site
import sys
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.db import transaction
from django.db.models import Q

user_site = site.getusersitepackages()
if isinstance(user_site, str) and user_site and user_site not in sys.path:
    sys.path.append(user_site)

from app.models import ImportacaoExtrato, RegraConciliador, StatusImportacao, TipoArquivo, TipoComparacao, TipoMovimento, TransacaoImportada


MONTH_LABELS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
DEFAULT_NORMALIZATION_OPTIONS = {
    "remover_numeros": True,
    "remover_especiais": True,
    "remover_acentos": True,
    "maiusculo": True,
    "colapsar_espacos": True,
}


@dataclass(slots=True)
class ParsedTransaction:
    linha_origem: int | None
    data_movimento: date
    descricao_original: str
    descricao_normalizada: str
    valor: Decimal
    tipo_movimento: str
    dados_brutos: dict[str, Any]


def detect_tipo_arquivo(filename: str) -> str:
    extension = Path(filename or "").suffix.lower()
    mapping = {
        ".csv": TipoArquivo.CSV,
        ".xls": TipoArquivo.XLS,
        ".xlsx": TipoArquivo.XLSX,
        ".pdf": TipoArquivo.PDF,
    }
    if extension not in mapping:
        raise ValueError("Arquivo inválido. Use CSV, XLS, XLSX ou PDF.")
    return mapping[extension]


def normalize_text(value: Any, *, options: dict[str, Any] | None = None) -> str:
    merged = {**DEFAULT_NORMALIZATION_OPTIONS, **(options or {})}
    text = str(value or "")
    text = unicodedata.normalize("NFKD", text)
    if merged.get("remover_acentos", True):
        text = "".join(char for char in text if not unicodedata.combining(char))

    if merged.get("remover_numeros", True):
        text = re.sub(r"\d+", " ", text)

    if merged.get("remover_especiais", True):
        text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)

    text = re.sub(r"[_/\\|\-]+", " ", text)
    if merged.get("colapsar_espacos", True):
        text = re.sub(r"\s+", " ", text)

    text = text.strip()
    if merged.get("maiusculo", True):
        text = text.upper()
    return text


def _read_uploaded_bytes(uploaded_file) -> bytes:
    uploaded_file.open("rb")
    try:
        return uploaded_file.read()
    finally:
        uploaded_file.close()


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _normalize_key(value: Any) -> str:
    return normalize_text(value, options={"remover_numeros": False, "remover_especiais": True, "maiusculo": True})


def _get_row_value(row: dict[str, Any], wanted_key: str | None) -> Any:
    if not wanted_key:
        return ""

    normalized_wanted = _normalize_key(wanted_key)
    for key, value in row.items():
        if _normalize_key(key) == normalized_wanted:
            return value

    return row.get(wanted_key, "")


def _parse_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")

    if isinstance(value, Decimal):
        return value

    if isinstance(value, (int, float)):
        return Decimal(str(value))

    text = str(value).strip()
    if not text:
        return Decimal("0")

    negative = text.startswith("(") and text.endswith(")")
    text = text.replace("R$", "").replace(" ", "")
    text = text.replace(".", "").replace(",", ".")
    text = re.sub(r"[^0-9.-]", "", text)

    if not text or text in {"-", "."}:
        return Decimal("0")

    try:
        amount = Decimal(text)
    except InvalidOperation:
        return Decimal("0")

    if negative or amount < 0:
        return abs(amount)

    return amount


def _value_is_negative(value: Any) -> bool:
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value)) < 0

    text = str(value or "").strip()
    return text.startswith("-") or (text.startswith("(") and text.endswith(")"))


def _movement_from_indicator(value: Any) -> str | None:
    indicator = normalize_text(value, options={"remover_numeros": False, "remover_especiais": True})
    if indicator in {"D", "DEBITO"}:
        return TipoMovimento.DEBITO
    if indicator in {"C", "CREDITO"}:
        return TipoMovimento.CREDITO
    return None


def _reference_date_from_importacao(importacao: ImportacaoExtrato) -> date | None:
    if not importacao.referencia:
        return None

    try:
        return datetime.strptime(f"{importacao.referencia}-01", "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_date(value: Any, *, date_format: str | None = None, reference_date: date | None = None) -> date:
    if isinstance(value, date):
        return value

    text = str(value or "").strip()
    if not text:
        raise ValueError("Data não encontrada na linha do extrato.")

    formats = []
    if date_format:
        formats.append(date_format)
    formats.extend(["%d/%m/%Y", "%d/%m/%y", "%d/%m/%Y %H:%M", "%d/%m/%y %H:%M", "%d/%m"])

    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt == "%d/%m" and reference_date:
                parsed = parsed.replace(year=reference_date.year)
            return parsed.date()
        except ValueError:
            continue

    raise ValueError(f"Não foi possível interpretar a data '{text}'.")


def _extract_csv_rows(uploaded_file) -> tuple[list[str], list[dict[str, Any]]]:
    content = _decode_text(_read_uploaded_bytes(uploaded_file))
    sample = content[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(content), dialect=dialect)
    headers = [header for header in (reader.fieldnames or []) if header]
    rows = [dict(row) for row in reader]
    return headers, rows


def _extract_xlsx_rows(uploaded_file) -> tuple[list[str], list[dict[str, Any]]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ValueError("Dependência para XLS/XLSX ausente. Instale openpyxl.") from exc

    workbook = load_workbook(filename=io.BytesIO(_read_uploaded_bytes(uploaded_file)), read_only=True, data_only=True)
    sheet = workbook.active
    iterator = sheet.iter_rows(values_only=True)
    first_row = next(iterator, ())
    headers = [str(value).strip() if value is not None else "" for value in first_row]
    if not any(headers):
        headers = [f"COLUNA_{index + 1}" for index in range(len(first_row))]

    rows: list[dict[str, Any]] = []
    for values in iterator:
        row = {}
        for index, header in enumerate(headers):
            row[header] = values[index] if index < len(values) else ""
        rows.append(row)

    return headers, rows


def _extract_pdf_text(uploaded_file) -> tuple[list[str], list[dict[str, Any]], str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ValueError("Dependência para PDF ausente. Instale pypdf.") from exc

    reader = PdfReader(io.BytesIO(_read_uploaded_bytes(uploaded_file)))
    text_parts = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        if page_text:
            text_parts.append(page_text)

    text = "\n".join(text_parts)
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    rows: list[dict[str, Any]] = []
    pattern = re.compile(
        r"(?P<data>\d{2}/\d{2}(?:/\d{2,4})?)\s+(?P<descricao>.+?)\s+(?P<valor>\(?-?[\d.]+,[\d]{2}\)?)(?:\s+(?P<tipo>[CD]))?$",
        flags=re.IGNORECASE,
    )
    for line_number, line in enumerate(lines, start=1):
        match = pattern.search(line)
        if not match:
            continue

        rows.append(
            {
                "DATA": match.group("data"),
                "DESCRICAO": match.group("descricao"),
                "VALOR": match.group("valor"),
                "TIPO": match.group("tipo") or "",
                "__line__": line_number,
            }
        )

    return lines, rows, text


def _resolve_mapped_column(mapping: dict[str, Any], key: str, headers: list[str]) -> str:
    value = mapping.get(key)
    if value:
        return str(value)

    aliases = {
        "data": ["DATA", "DATA_MOVIMENTO", "DATA LANÇAMENTO", "DATA LANCAMENTO", "MOVIMENTO", "DATE"],
        "descricao": ["DESCRICAO", "HISTORICO", "HISTORICO LANCAMENTO", "LANÇAMENTO", "LANCAMENTO", "DESCRIPTION"],
        "valor": ["VALOR", "AMOUNT", "VLR", "DEBITO", "CREDITO"],
        "credito": ["CREDITO", "CRÉDITO", "CREDIT"],
        "debito": ["DEBITO", "DÉBITO", "DEBIT"],
    }

    normalized_headers = { _normalize_key(header): header for header in headers }
    for alias in aliases.get(key, []):
        alias_key = _normalize_key(alias)
        if alias_key in normalized_headers:
            return normalized_headers[alias_key]
    return ""


def _build_transaction_row(
    row: dict[str, Any],
    *,
    linha_origem: int | None,
    importacao: ImportacaoExtrato,
    headers: list[str],
    configuracao: dict[str, Any],
) -> ParsedTransaction:
    colunas = configuracao.get("colunas") or {}
    date_format = configuracao.get("data_format") or configuracao.get("formato_data") or ""
    normalizacao = configuracao.get("normalizacao") or {}
    referencia = _reference_date_from_importacao(importacao)

    data_column = _resolve_mapped_column(colunas, "data", headers)
    descricao_column = _resolve_mapped_column(colunas, "descricao", headers)
    valor_column = _resolve_mapped_column(colunas, "valor", headers)
    credito_column = _resolve_mapped_column(colunas, "credito", headers)
    debito_column = _resolve_mapped_column(colunas, "debito", headers)

    raw_data = _get_row_value(row, data_column)
    raw_descricao = _get_row_value(row, descricao_column)
    raw_valor = _get_row_value(row, valor_column)
    raw_credito = _get_row_value(row, credito_column)
    raw_debito = _get_row_value(row, debito_column)
    raw_tipo = _get_row_value(row, "TIPO") or _get_row_value(row, "TIPO_MOVIMENTO")

    descricao_original = str(raw_descricao or raw_valor or "").strip()
    if not descricao_original:
        descricao_original = str(row.get("DESCRICAO") or row.get("DESCRIÇÃO") or row.get("HISTORICO") or "").strip()

    if raw_credito not in {None, ""}:
        tipo_movimento = TipoMovimento.CREDITO
        valor = _parse_decimal(raw_credito)
    elif raw_debito not in {None, ""}:
        tipo_movimento = TipoMovimento.DEBITO
        valor = _parse_decimal(raw_debito)
    else:
        valor = _parse_decimal(raw_valor)
        tipo_movimento = _movement_from_indicator(raw_tipo)
        if not tipo_movimento:
            tipo_movimento = TipoMovimento.DEBITO if _value_is_negative(raw_valor) else TipoMovimento.CREDITO

    data_movimento = _parse_date(raw_data, date_format=date_format or None, reference_date=referencia)
    descricao_normalizada = normalize_text(descricao_original, options=normalizacao or None)

    return ParsedTransaction(
        linha_origem=linha_origem,
        data_movimento=data_movimento,
        descricao_original=descricao_original,
        descricao_normalizada=descricao_normalizada,
        valor=abs(valor),
        tipo_movimento=tipo_movimento,
        dados_brutos={
            **row,
            "__linha_origem__": linha_origem,
        },
    )


def inspect_importacao_file(importacao: ImportacaoExtrato) -> dict[str, Any]:
    if importacao.tipo_arquivo == TipoArquivo.CSV:
        headers, rows = _extract_csv_rows(importacao.arquivo)
        return {
            "cabecalhos": headers,
            "amostra": rows[:5],
            "tipo": importacao.tipo_arquivo,
        }

    if importacao.tipo_arquivo in {TipoArquivo.XLS, TipoArquivo.XLSX}:
        headers, rows = _extract_xlsx_rows(importacao.arquivo)
        return {
            "cabecalhos": headers,
            "amostra": rows[:5],
            "tipo": importacao.tipo_arquivo,
        }

    linhas, rows, text = _extract_pdf_text(importacao.arquivo)
    return {
        "linhas_extraidas": len(linhas),
        "amostra": rows[:5],
        "texto_preview": text[:4000],
        "tipo": importacao.tipo_arquivo,
    }


def _parse_importacao_rows(importacao: ImportacaoExtrato, configuracao: dict[str, Any]) -> tuple[list[ParsedTransaction], dict[str, Any]]:
    configuracao = configuracao or {}
    if importacao.tipo_arquivo == TipoArquivo.CSV:
        headers, rows = _extract_csv_rows(importacao.arquivo)
        parsed = [
            _build_transaction_row(row, linha_origem=index, importacao=importacao, headers=headers, configuracao=configuracao)
            for index, row in enumerate(rows, start=1)
            if any(str(value).strip() for value in row.values())
        ]
        return parsed, {"cabecalhos": headers, "amostra": rows[:5], "tipo": importacao.tipo_arquivo}

    if importacao.tipo_arquivo in {TipoArquivo.XLS, TipoArquivo.XLSX}:
        headers, rows = _extract_xlsx_rows(importacao.arquivo)
        parsed = [
            _build_transaction_row(row, linha_origem=index, importacao=importacao, headers=headers, configuracao=configuracao)
            for index, row in enumerate(rows, start=1)
            if any(str(value).strip() for value in row.values())
        ]
        return parsed, {"cabecalhos": headers, "amostra": rows[:5], "tipo": importacao.tipo_arquivo}

    linhas, rows, text = _extract_pdf_text(importacao.arquivo)
    parsed = []
    for index, row in enumerate(rows, start=1):
        parsed.append(_build_transaction_row(row, linha_origem=index, importacao=importacao, headers=list(row.keys()), configuracao=configuracao))
    return parsed, {
        "linhas_extraidas": len(linhas),
        "amostra": rows[:5],
        "texto_preview": text[:4000],
        "tipo": importacao.tipo_arquivo,
    }


def process_importacao(importacao: ImportacaoExtrato, configuracao: dict[str, Any] | None = None) -> dict[str, Any]:
    with transaction.atomic():
        parsed_rows, metadata = _parse_importacao_rows(importacao, configuracao or {})
        if not parsed_rows:
            importacao.status = StatusImportacao.ERRO
            importacao.mensagem_erro = "Nenhuma transação válida foi encontrada no arquivo."
            importacao.metadados = metadata
            importacao.save(update_fields=["status", "mensagem_erro", "metadados", "atualizado_em"])
            raise ValueError(importacao.mensagem_erro)

        existing = {transaction.linha_origem: transaction for transaction in importacao.transacoes.all()}
        seen_lines: set[int] = set()

        for parsed in parsed_rows:
            line_number = parsed.linha_origem
            if line_number is not None:
                seen_lines.add(line_number)

            transaction_obj = existing.get(line_number)
            defaults = {
                "data_movimento": parsed.data_movimento,
                "descricao_original": parsed.descricao_original,
                "descricao_normalizada": parsed.descricao_normalizada,
                "valor": parsed.valor,
                "tipo_movimento": parsed.tipo_movimento,
                "dados_brutos": parsed.dados_brutos,
            }

            if transaction_obj:
                if transaction_obj.revisado_manual:
                    defaults.update(
                        {
                            "descricao_normalizada": transaction_obj.descricao_normalizada,
                            "categoria": transaction_obj.categoria,
                            "subcategoria": transaction_obj.subcategoria,
                            "conta_debito": transaction_obj.conta_debito,
                            "conta_credito": transaction_obj.conta_credito,
                            "codigo_historico": transaction_obj.codigo_historico,
                            "regra_aplicada": transaction_obj.regra_aplicada,
                            "revisado_manual": True,
                        }
                    )
                else:
                    defaults.update(
                        {
                            "regra_aplicada": None,
                            "categoria": "",
                            "subcategoria": "",
                            "conta_debito": "",
                            "conta_credito": "",
                            "codigo_historico": "",
                            "revisado_manual": False,
                        }
                    )

            TransacaoImportada.objects.update_or_create(
                importacao=importacao,
                linha_origem=line_number,
                defaults=defaults,
            )

        if seen_lines:
            importacao.transacoes.exclude(linha_origem__in=seen_lines).filter(revisado_manual=False).delete()

        importacao.status = StatusImportacao.PROCESSADA
        importacao.mensagem_erro = ""
        importacao.metadados = metadata
        importacao.save(update_fields=["status", "mensagem_erro", "metadados", "atualizado_em"])

        return {
            "transacoes_processadas": len(parsed_rows),
            "cabecalhos": metadata.get("cabecalhos", []),
            "amostra": metadata.get("amostra", []),
            "tipo_arquivo": importacao.tipo_arquivo,
        }


def _rule_matches_description(rule: RegraConciliador, description: str) -> bool:
    reference = normalize_text(rule.texto_referencia)
    if not reference:
        return False

    target = normalize_text(description)
    if rule.tipo_comparacao == TipoComparacao.IGUAL:
        return target == reference

    if rule.tipo_comparacao == TipoComparacao.COMECA_COM:
        return target.startswith(reference)

    return reference in target


def _rule_matches_transaction(rule: RegraConciliador, transaction: TransacaoImportada) -> bool:
    if not rule.ativo or not rule.aplicar_automatico:
        return False

    if rule.empresa_id and rule.empresa_id != transaction.importacao.empresa_id:
        return False

    if rule.escritorio_id != transaction.importacao.escritorio_id:
        return False

    if rule.tipo_movimento != TipoMovimento.AMBOS and rule.tipo_movimento != transaction.tipo_movimento:
        return False

    description = transaction.descricao_normalizada or transaction.descricao_original
    return _rule_matches_description(rule, description)


def apply_rules_to_importacao(importacao: ImportacaoExtrato) -> dict[str, Any]:
    with transaction.atomic():
        rules_queryset = RegraConciliador.objects.filter(escritorio=importacao.escritorio, ativo=True).filter(
            Q(empresa__isnull=True) | Q(empresa=importacao.empresa)
        )
        rules = list(rules_queryset.order_by("prioridade", "nome", "criado_em"))

        applied = 0
        pending = 0
        manual = 0

        for transaction_obj in importacao.transacoes.select_related("regra_aplicada").order_by("data_movimento", "id"):
            if transaction_obj.revisado_manual:
                manual += 1
                continue

            matched_rule = next((rule for rule in rules if _rule_matches_transaction(rule, transaction_obj)), None)

            if matched_rule:
                transaction_obj.regra_aplicada = matched_rule
                transaction_obj.categoria = matched_rule.categoria
                transaction_obj.subcategoria = matched_rule.subcategoria
                transaction_obj.conta_debito = matched_rule.conta_debito
                transaction_obj.conta_credito = matched_rule.conta_credito
                transaction_obj.codigo_historico = matched_rule.codigo_historico
                applied += 1
            else:
                transaction_obj.regra_aplicada = None
                transaction_obj.categoria = ""
                transaction_obj.subcategoria = ""
                transaction_obj.conta_debito = ""
                transaction_obj.conta_credito = ""
                transaction_obj.codigo_historico = ""
                pending += 1

            transaction_obj.save(
                update_fields=[
                    "regra_aplicada",
                    "categoria",
                    "subcategoria",
                    "conta_debito",
                    "conta_credito",
                    "codigo_historico",
                    "atualizado_em",
                ]
            )

        return {
            "regras_aplicadas": applied,
            "pendentes": pending,
            "manuais": manual,
            "regras_disponiveis": len(rules),
        }
