from __future__ import annotations

import io
import re
import site
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

user_site = site.getusersitepackages()
if isinstance(user_site, str) and user_site and user_site not in sys.path:
    sys.path.append(user_site)


def _read_file_bytes(uploaded_file) -> bytes:
    uploaded_file.open("rb")
    try:
        return uploaded_file.read()
    finally:
        uploaded_file.close()


def _parse_brl_decimal(value: Any) -> Decimal:
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
    text = re.sub(r"[^0-9.\-]", "", text)

    if not text or text in {"-", "."}:
        return Decimal("0")

    try:
        amount = Decimal(text)
    except InvalidOperation:
        return Decimal("0")

    return abs(amount) if negative else amount


def _parse_date_br(text: str, ref_date: date | None = None) -> date | None:
    text = text.strip()
    if not text:
        return None

    formats = ["%d/%m/%Y", "%d/%m/%y", "%d/%m/%Y %H:%M", "%d/%m/%y %H:%M", "%d/%m"]

    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt == "%d/%m" and ref_date:
                parsed = parsed.replace(year=ref_date.year, month=ref_date.month)
            return parsed.date()
        except ValueError:
            continue

    return None


@dataclass(slots=True)
class ExtratoHeader:
    empresa_nome: str = ""
    empresa_cnpj: str = ""
    agencia: str = ""
    conta: str = ""
    saldo: Decimal = field(default_factory=lambda: Decimal("0"))
    periodo_inicio: date | None = None
    periodo_fim: date | None = None
    dados_brutos: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LancamentoExtrato:
    linha_origem: int = 0
    pagina: int = 0
    data: date | None = None
    descricao_original: str = ""
    documento: str = ""
    valor: Decimal = field(default_factory=lambda: Decimal("0"))
    natureza_inferida: str = ""
    saldo: Decimal | None = None
    linha_original: str = ""


@dataclass(slots=True)
class ExtratoResult:
    success: bool = False
    header: ExtratoHeader = field(default_factory=ExtratoHeader)
    lancamentos: list[LancamentoExtrato] = field(default_factory=list)
    total_lancamentos: int = 0
    avisos: list[str] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)


class PDFExtratoParser:
    def __init__(self):
        self._text: str = ""
        self._lines: list[str] = []
        self._warnings: list[str] = []
        self._errors: list[str] = []

    def parse(self, uploaded_file) -> ExtratoResult:
        try:
            from pypdf import PdfReader
        except ImportError:
            return ExtratoResult(
                success=False,
                erros=["Biblioteca pypdf não instalada. Execute: pip install pypdf"]
            )

        try:
            reader = PdfReader(io.BytesIO(_read_file_bytes(uploaded_file)))
        except Exception as e:
            return ExtratoResult(
                success=False,
                erros=[f"Erro ao ler PDF: {str(e)}"]
            )

        text_parts = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)

        self._text = "\n".join(text_parts)
        self._lines = [
            line.strip() for line in self._text.splitlines() if line.strip()
        ]

        if not self._lines:
            return ExtratoResult(
                success=False,
                erros=["Nenhum texto encontrado no PDF."]
            )

        header = self._extract_header()
        lancamentos = self._extract_lancamentos()

        result = ExtratoResult(
            success=True,
            header=header,
            lancamentos=lancamentos,
            total_lancamentos=len(lancamentos),
            avisos=self._warnings,
            erros=self._errors,
        )

        return result

    def _extract_header(self) -> ExtratoHeader:
        header = ExtratoHeader()
        full_text = self._text

        cnpj_patterns = [
            r"CNPJ[:\s]*(\d{2}[\.\d]{2}[\.\d]{3}[\/]?\d{4}[\-\d]{2})",
            r"(\d{2}[\.\d]{2}[\.\d]{3}[\/]?\d{4}[\-\d]{2})",
        ]
        for pattern in cnpj_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                header.empresa_cnpj = match.group(1)
                break

        agencia_patterns = [
            r"AG[\s:]*(?:Ncia|NCO)?[:\s]*(\d+)",
            r"AGÊNCIA[:\s]*(\d+)",
            r"AG[:\s]*(\d+)",
        ]
        for pattern in agencia_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                header.agencia = match.group(1)
                break

        conta_patterns = [
            r"CONTA[:\s]*(\d+[\-\d]*)",
            r"CC[:\s]*(\d+[\-\d]*)",
            r"Conta Corrente[:\s]*(\d+[\-\d]*)",
        ]
        for pattern in conta_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                header.conta = match.group(1)
                break

        saldo_patterns = [
            r"SALDO[:\s]*DISPON[ÍI]VEL[:\s]*R\$?\s*([\d.,]+)",
            r"SALDO\s*ATUAL[:\s]*R\$?\s*([\d.,]+)",
            r"Saldo[:\s]*R\$?\s*([\d.,]+)",
            r"([\d.,]{2,})\s*$",
        ]
        for pattern in saldo_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                saldo_str = match.group(1)
                header.saldo = _parse_brl_decimal(saldo_str)
                break

        nome_lines = []
        cnpj_pos = -1
        for i, line in enumerate(self._lines):
            if header.empresa_cnpj and header.empresa_cnpj in line:
                cnpj_pos = i
            if i < cnpj_pos + 3 and i > 0:
                nome_lines.append(line)

        if nome_lines:
            nome = nome_lines[0]
            if header.empresa_cnpj in nome:
                nome = nome.replace(header.empresa_cnpj, "").strip()
            if nome:
                header.empresa_nome = nome.strip("- ").strip()

        periodo_pattern = r"PER[ÍI]ODO[:\s]*(\d{2}/\d{2}/\d{4})\s*[àa]\s*(\d{2}/\d{2}/\d{4})"
        match = re.search(periodo_pattern, full_text, re.IGNORECASE)
        if match:
            header.periodo_inicio = _parse_date_br(match.group(1))
            header.periodo_fim = _parse_date_br(match.group(2))

        header.dados_brutos = {
            "linhas_extraidas": len(self._lines),
            "caracteres": len(self._text),
        }

        return header

    def _extract_lancamentos(self) -> list[LancamentoExtrato]:
        lancamentos = []
        seen_lines: set[str] = set()

        date_pattern = re.compile(r"(\d{2}/\d{2}(?:/\d{2,4})?)[\s\(]?)")
        value_pattern = re.compile(r"(?:-?[\d.]+,\d{2}|\d{1,3}(?:\.\d{3})*,\d{2})")

        for line_idx, line in enumerate(self._lines):
            line_clean = re.sub(r"\s+", " ", line)

            date_match = date_pattern.search(line_clean)
            if not date_match:
                continue

            date_str = date_match.group(1).strip()
            data = _parse_date_br(date_str)
            if not data:
                continue

            remaining = line_clean[date_match.end():].strip()

            value_matches = list(value_pattern.finditer(remaining))
            if not value_matches:
                continue

            last_match = value_matches[-1]
            valor_text = last_match.group()
            valor = _parse_brl_decimal(valor_text)

            descricao = remaining[:last_match.start()].strip()

            remaining_after_value = remaining[last_match.end():].strip()
            documento = ""
            saldo = None

            next_part = remaining_after_value[:30] if remaining_after_value else ""
            if next_part and next_part[0].isdigit() and "/" not in next_part[:10]:
                pass
            else:
                parts = remaining_after_value.split()
                potencial_saldo = None
                for part in parts:
                    potential = _parse_brl_decimal(part)
                    if potential and potential != valor:
                        potencial_saldo = potential
                        break

                if potencial_saldo is not None:
                    saldo = potencial_saldo
                    doc_parts = []
                    for part in parts:
                        if _parse_brl_decimal(part) == potencial_saldo:
                            doc_parts.append(part)
                        else:
                            documento += part + " "
                    documento = documento.strip()

            line_key = f"{data}|{descricao[:20]}|{valor}"
            if line_key in seen_lines:
                continue
            seen_lines.add(line_key)

            natureza = ""
            if valor > 0:
                after_value_pos = remaining.find(valor_text)
                if after_value_pos > 0:
                    next_chars = remaining[after_value_pos + len(valor_text):after_value_pos + len(valor_text) + 10].upper()
                    if "C" in next_chars and "D" not in next_chars:
                        natureza = "CREDITO"
                    elif "D" in next_chars and "C" not in next_chars:
                        natureza = "DEBITO"

            if not natureza:
                context_upper = line_clean.upper()
                if "CRÉDITO" in context_upper or "DEPOSIT" in context_upper or "TRANSF. CR" in context_upper:
                    natureza = "CREDITO"
                elif "DÉBITO" in context_upper or "TARIFA" in context_upper or "TRANSF. DB" in context_upper or "PIX ENVIADO" in context_upper:
                    natureza = "DEBITO"

            lancamento = LancamentoExtrato(
                linha_origem=line_idx + 1,
                pagina=1,
                data=data,
                descricao_original=descricao,
                documento=documento,
                valor=abs(valor),
                natureza_inferida=natureza,
                saldo=saldo,
                linha_original=line,
            )
            lancamentos.append(lancamento)

        lancamentos.sort(key=lambda x: (x.data or date.max, x.linha_origem))

        return lancamentos


def process_extrato_pdf(uploaded_file) -> ExtratoResult:
    parser = PDFExtratoParser()
    return parser.parse(uploaded_file)