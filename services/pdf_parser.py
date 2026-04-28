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

        date_pattern = re.compile(r"(\d{2}/\d{2}(?:/\d{2,4})?)[\s(]?")
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


class BradescoExtratoParser:
    """
    Parser especializado para extrato Bradesco Net Empresa (PDF).
    Formato de colunas: Data | Lançamento | Dcto. | Crédito (R$) | Débito (R$) | Saldo (R$)
    A natureza é determinada por qual coluna (Crédito ou Débito) contém o valor.
    """

    BANCOS_SUPORTADOS = ["bradesco"]

    # Linha de cabeçalho do extrato — usado para detectar o início dos lançamentos
    _HEADER_RE = re.compile(r"Data\s+Lan[çc]amento\s+Dcto", re.IGNORECASE)

    # Linha com data no início: DD/MM/YYYY
    _DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+(.+)$")

    # Valor BRL: números com pontos/vírgulas ex: 1.234,56 ou 25.151,62
    _VALUE_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})")

    def __init__(self):
        self._text: str = ""
        self._pages: list[str] = []
        self._warnings: list[str] = []
        self._errors: list[str] = []

    def parse(self, uploaded_file) -> "ExtratoResult":
        try:
            from pypdf import PdfReader
        except ImportError:
            return ExtratoResult(
                success=False,
                erros=["Biblioteca pypdf não instalada. Execute: pip install pypdf"]
            )

        try:
            reader = PdfReader(io.BytesIO(_read_file_bytes(uploaded_file)))
        except Exception as exc:
            return ExtratoResult(success=False, erros=[f"Erro ao ler PDF: {exc}"])

        self._pages = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            self._pages.append(page_text)

        self._text = "\n".join(self._pages)

        if not self._text.strip():
            return ExtratoResult(success=False, erros=["Nenhum texto encontrado no PDF."])

        header = self._extract_header()
        lancamentos = self._extract_lancamentos()

        return ExtratoResult(
            success=True,
            header=header,
            lancamentos=lancamentos,
            total_lancamentos=len(lancamentos),
            avisos=self._warnings,
            erros=self._errors,
        )

    def _extract_header(self) -> "ExtratoHeader":
        header = ExtratoHeader()
        full_text = self._text

        # Empresa: linha com "| CNPJ:"
        m = re.search(r"([A-Z][^\n|]+?)\s*\|\s*CNPJ[:\s]*([\d./-]+)", full_text)
        if m:
            header.empresa_nome = m.group(1).strip()
            header.empresa_cnpj = m.group(2).strip()
        else:
            # Fallback: CNPJ isolado
            m2 = re.search(r"CNPJ[:\s]*([\d]{2}[\.\d]{11}[\/]?\d{4}[-]?\d{2})", full_text, re.IGNORECASE)
            if m2:
                header.empresa_cnpj = m2.group(1)

        # Agência e conta
        m = re.search(r"AG[:\s]*(\d+)\s*\|\s*(?:CC|Conta)[:\s]*([\d-]+)", full_text, re.IGNORECASE)
        if m:
            header.agencia = m.group(1)
            header.conta = m.group(2)

        # Período
        m = re.search(r"Entre\s+(\d{2}/\d{2}/\d{4})\s+e\s+(\d{2}/\d{2}/\d{4})", full_text, re.IGNORECASE)
        if m:
            header.periodo_inicio = _parse_date_br(m.group(1))
            header.periodo_fim = _parse_date_br(m.group(2))

        # Saldo disponível
        m = re.search(r"Total\s+Dispon[íi]vel\s*\(R\$\)\s*([\d.,]+)", full_text, re.IGNORECASE)
        if m:
            header.saldo = _parse_brl_decimal(m.group(1))

        header.dados_brutos = {"banco": "bradesco", "paginas": len(self._pages)}
        return header

    def _extract_lancamentos(self) -> list["LancamentoExtrato"]:
        """
        Estratégia: percorre todas as linhas e agrupa blocos por data.
        Cada bloco de data pode ter múltiplas linhas de lançamento.
        A última linha do bloco (antes da próxima data) tende a ter os valores numéricos.
        
        O extrato Bradesco tem estrutura:
          02/01/2024  CARTAO VISA ELECTRON
                      CIELO S.A - INSTITUICAO DE PAG  3723000  672,74   25.824,36
        
        Lógica de natureza:
        - Uma linha tem 2 valores no final → penúltimo = valor, último = saldo
        - Se o penúltimo é menor que o saldo e saldo > saldo_anterior, provavelmente crédito
        - Mas o melhor indicador é a posição horizontal (crédito antes de débito na linha)
        - Usamos heurística: se saldo aumentou em relação ao anterior → CREDITO, senão → DEBITO
        """
        lancamentos: list[LancamentoExtrato] = []
        lines = [line.strip() for line in self._text.splitlines()]

        # Remove linhas de cabeçalho / rodapé
        _SKIP_RE = re.compile(
            r"^(Folha|Extrato\s+Mensal|A\s+MESQUITA|Nome\s+do|Data\s+da|Data\s+Lan[çc]|Ag[êe]ncia\s*\|"
            r"|Agência\s*\|\s*Conta|Total\s+Dispon|Últimos\s+Lançamentos|SALDO\s+ANTERIOR|Os\s+dados\s+acima"
            r"|Não\s+há\s+lan|Saldos\s+Invest|^\s*$)", re.IGNORECASE
        )

        # Linhas de resumo/totais que aparecem com data mas não são lançamentos reais
        _SUMMARY_RE = re.compile(r"^Total\b", re.IGNORECASE)

        # Acumular blocos: (data_str, [linhas do bloco])
        blocks: list[tuple[str, list[str]]] = []
        current_date: str | None = None
        current_block: list[str] = []

        for line in lines:
            if _SKIP_RE.search(line):
                continue

            m = self._DATE_RE.match(line)
            if m:
                rest = m.group(2).strip()
                # Ignorar linhas de resumo que começam com data mas descrevem totais
                if _SUMMARY_RE.match(rest):
                    continue
                if current_date and current_block:
                    blocks.append((current_date, current_block))
                current_date = m.group(1)
                current_block = [rest] if rest else []
            elif current_date is not None:
                current_block.append(line)

        if current_date and current_block:
            blocks.append((current_date, current_block))

        # Processar cada bloco
        prev_saldo: Decimal | None = None
        line_idx = 0

        for date_str, block_lines in blocks:
            data = _parse_date_br(date_str)
            if not data:
                continue

            # Agrupar sub-lançamentos dentro do bloco
            # Heurística: uma linha de lançamento termina quando encontramos valores monetários
            sub_lancamentos = self._split_block_into_lancamentos(block_lines)

            for sub in sub_lancamentos:
                line_idx += 1
                descricao = sub["descricao"]
                documento = sub["documento"]
                valor = sub["valor"]
                saldo = sub["saldo"]

                if valor <= 0:
                    continue

                # Determinar natureza pela variação de saldo
                natureza = sub.get("natureza", "")
                if not natureza and saldo is not None and prev_saldo is not None:
                    diff = saldo - prev_saldo
                    if diff > 0:
                        natureza = "CREDITO"
                    else:
                        natureza = "DEBITO"
                elif not natureza:
                    # Palavras-chave na descrição
                    desc_upper = descricao.upper()
                    credito_kw = ("DEPOSITO", "DEP ", "RECEBI", "CREDITO", "CRÉDITO", "VENDA CART",
                                  "CIELO", "PIX QR CODE", "TRANSFERENCIA PIX\nREM:", "REM:")
                    debito_kw = ("PAGTO", "PAGAMENTO", "TRANSF CC", "TARIFA", "GASTOS CART",
                                 "OPERACAO CAPITAL", "DEBITO", "DÉBITO", "DES:")
                    if any(kw in desc_upper for kw in credito_kw):
                        natureza = "CREDITO"
                    elif any(kw in desc_upper for kw in debito_kw):
                        natureza = "DEBITO"

                if saldo is not None:
                    prev_saldo = saldo

                lancamentos.append(LancamentoExtrato(
                    linha_origem=line_idx,
                    pagina=1,
                    data=data,
                    descricao_original=descricao,
                    documento=documento,
                    valor=valor,
                    natureza_inferida=natureza,
                    saldo=saldo,
                    linha_original=" | ".join(block_lines[:3]),
                ))

        return lancamentos

    def _split_block_into_lancamentos(self, block_lines: list[str]) -> list[dict]:
        """
        Divide as linhas de um bloco (mesmo dia) em sub-lançamentos.
        Cada sub-lançamento é detectado quando uma linha contém valores monetários.
        """
        results = []
        pending_desc_lines: list[str] = []

        for line in block_lines:
            values = self._VALUE_RE.findall(line)
            if not values:
                # Linha de descrição pura
                pending_desc_lines.append(line)
                continue

            # Linha com valores — extrai documento e valores
            # Remove os valores do texto para obter a parte descritiva
            desc_part = self._VALUE_RE.sub("", line).strip()
            # Remove espaços extras
            desc_part = re.sub(r"\s{2,}", " ", desc_part).strip()

            # Juntar com linhas de descrição acumuladas
            full_desc_lines = pending_desc_lines + ([desc_part] if desc_part else [])
            pending_desc_lines = []

            # Separar documento da descrição
            # Documento: token numérico de 4-10 dígitos no final da parte descritiva
            documento = ""
            full_desc = " ".join(full_desc_lines).strip()
            doc_match = re.search(r"\b(\d{4,10})\s*$", full_desc)
            if doc_match:
                documento = doc_match.group(1)
                full_desc = full_desc[:doc_match.start()].strip()

            # Valores: o Bradesco tem [opcional: valor] [saldo]
            # A última linha de valores geralmente tem: crédito_ou_débito, saldo
            # Se há 1 valor → é o saldo (lançamento sem valor explícito = linha de continuação)
            # Se há 2+ valores → penúltimo = valor do lançamento, último = saldo
            decimals = [_parse_brl_decimal(v) for v in values]

            if len(decimals) == 1:
                # Só saldo — linha de continuação ou lançamento sem valor separado
                # Pode ser que o valor esteja no valor negativo (débito)
                saldo = decimals[0]
                valor = Decimal("0")
            elif len(decimals) >= 2:
                valor = decimals[-2]
                saldo = decimals[-1]
            else:
                continue

            # Heurística: se há sinal negativo original na linha, é débito
            natureza = ""
            raw_values_in_line = self._VALUE_RE.findall(line)
            if raw_values_in_line and len(raw_values_in_line) >= 2:
                # Checar se o valor (penúltimo) aparece precedido de "-" na linha original
                penultimate_str = raw_values_in_line[-2]
                idx_in_line = line.rfind(penultimate_str)
                if idx_in_line > 0 and line[idx_in_line - 1] == "-":
                    natureza = "DEBITO"
                    valor = _parse_brl_decimal(penultimate_str)

            results.append({
                "descricao": full_desc,
                "documento": documento,
                "valor": abs(valor) if valor > 0 else Decimal("0"),
                "saldo": saldo,
                "natureza": natureza,
            })

        return results


def process_extrato_pdf(uploaded_file, banco: str = "auto") -> ExtratoResult:
    """
    Processa um extrato bancário em PDF.
    Se banco='bradesco' (ou detectado automaticamente pelo conteúdo), usa o parser especializado.
    Lê os bytes uma única vez para evitar I/O operation on closed file.
    """
    # Ler bytes uma única vez
    try:
        raw: bytes = _read_file_bytes(uploaded_file)
    except Exception as exc:
        return ExtratoResult(success=False, erros=[f"Erro ao ler arquivo: {exc}"])

    # Detectar banco pelo conteúdo se modo automático
    if banco == "auto":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            first_page = reader.pages[0].extract_text() or "" if reader.pages else ""
            if "bradesco" in first_page.lower() or "net empresa" in first_page.lower():
                banco = "bradesco"
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
        parser = BradescoExtratoParser()
        return parser.parse(wrapped)

    parser = PDFExtratoParser()
    return parser.parse(wrapped)