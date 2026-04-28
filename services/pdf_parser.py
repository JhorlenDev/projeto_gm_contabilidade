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
    Detecta automaticamente o banco pelo conteúdo ou usa o parser indicado.
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


# ──────────────────────────────────────────────────────────────────────────────
# Parser — Banco da Amazônia (GESOP / PD_CCOR)
# Formato: DATA | NR DOC | HISTÓRICO | VALOR LANCTO | D/C | SALDO
# ──────────────────────────────────────────────────────────────────────────────

class AmazoniaExtratoParser:
    """
    Extrato mensal do Banco da Amazônia — sistema GESOP.
    Layout esperado após extração pypdf:
      02/01/24 026577 1127 - AUTOMATIZACAO TARIFA MANUTENCAO PJ -45,00 D 40.318,30
    Aceita anos com 2 ou 4 dígitos. Suporta extração por bloco (cada coluna em linha separada).
    """

    # Aceita DD/MM/YY e DD/MM/YYYY
    _DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{2,4})(?:\s+(.+))?$")
    _VALUE_RE = re.compile(r"-?[\d.]+,\d{2}")
    # D/C pode estar no final da linha (sem trailing number)
    _DC_RE = re.compile(r"([\d.,]+)\s+([DC])(?:\s+[\d.,]+|\s*$)")

    def parse(self, uploaded_file) -> ExtratoResult:
        try:
            from pypdf import PdfReader
        except ImportError:
            return ExtratoResult(success=False, erros=["pypdf não instalado."])
        try:
            reader = PdfReader(io.BytesIO(_read_file_bytes(uploaded_file)))
        except Exception as exc:
            return ExtratoResult(success=False, erros=[f"Erro ao ler PDF: {exc}"])

        pages = [p.extract_text() or "" for p in reader.pages]
        full_text = "\n".join(pages)
        lines = [l.strip() for l in full_text.splitlines() if l.strip()]

        header = self._extract_header(full_text)
        lancamentos = self._extract_lancamentos(lines)

        return ExtratoResult(
            success=True,
            header=header,
            lancamentos=lancamentos,
            total_lancamentos=len(lancamentos),
        )

    def _extract_header(self, text: str) -> ExtratoHeader:
        h = ExtratoHeader()
        h.dados_brutos = {"banco": "amazonia"}

        m = re.search(r"Titular\s*:\s*([\d./\-]+)\s*-\s*(.+)", text, re.IGNORECASE)
        if m:
            h.empresa_cnpj = m.group(1).strip()
            h.empresa_nome = m.group(2).strip().split("\n")[0].strip()

        m = re.search(r"Agência\s*:\s*(\d+)", text, re.IGNORECASE)
        if m:
            h.agencia = m.group(1)

        m = re.search(r"Conta\s*:\s*([\d\-]+)", text, re.IGNORECASE)
        if m:
            h.conta = m.group(1)

        # Saldo inicial
        m = re.search(r"Saldo\s+Dispon[íi]vel\s+Inicial[:\s]*([\d.,]+)", text, re.IGNORECASE)
        if m:
            h.saldo = _parse_brl_decimal(m.group(1))

        # Período da referência (ex: "01 / 2024")
        m = re.search(r"(\d{2})\s*/\s*(\d{4})", text)
        if m:
            try:
                h.periodo_inicio = date(int(m.group(2)), int(m.group(1)), 1)
                import calendar
                last_day = calendar.monthrange(int(m.group(2)), int(m.group(1)))[1]
                h.periodo_fim = date(int(m.group(2)), int(m.group(1)), last_day)
            except Exception:
                pass

        return h

    def _extract_lancamentos(self, lines: list[str]) -> list[LancamentoExtrato]:
        """
        Acumula linhas por bloco de data para suportar tanto o layout em linha única
        (todos os campos numa linha) quanto o layout multi-linha onde pypdf extrai
        cada coluna em linhas separadas.
        """
        lancamentos = []

        _SKIP_RE = re.compile(
            r"^(Total\s+de|Data\s+da|Hora\s+da|Emitido|Para\s+simples|Vencto|Tipo\s+Conta"
            r"|DATA\s+NR|Saldo\s+Dispon|PD_CCOR|GESOP)",
            re.IGNORECASE,
        )

        # Fase 1: agrupar linhas em blocos por data
        blocks: list[tuple[str, str, list[str]]] = []  # (date_str, first_rest, extra_lines)
        current_date: str | None = None
        current_first: str = ""
        current_extra: list[str] = []

        for line in lines:
            if _SKIP_RE.search(line):
                continue
            m = self._DATE_RE.match(line)
            if m:
                if current_date is not None:
                    blocks.append((current_date, current_first, current_extra))
                current_date = m.group(1)
                current_first = (m.group(2) or "").strip()
                current_extra = []
            elif current_date is not None:
                current_extra.append(line)

        if current_date is not None:
            blocks.append((current_date, current_first, current_extra))

        # Fase 2: extrair lançamento de cada bloco
        line_idx = 0
        for date_str, first_rest, extra_lines in blocks:
            data = _parse_date_br(date_str)
            if not data:
                continue

            # Junta toda a informação do bloco numa string só
            full = " ".join([first_rest] + extra_lines).strip()
            if not full:
                continue

            values = self._VALUE_RE.findall(full)
            if not values:
                continue

            # Último = saldo; penúltimo = valor lançado
            saldo_str = values[-1]
            valor_str = values[-2] if len(values) >= 2 else values[-1]

            saldo = _parse_brl_decimal(saldo_str)
            valor = abs(_parse_brl_decimal(valor_str))

            if valor <= 0:
                continue

            # Detecta D/C com ou sem número depois
            dc_match = self._DC_RE.search(full)
            if dc_match:
                natureza = "DEBITO" if dc_match.group(2) == "D" else "CREDITO"
            elif valor_str.startswith("-"):
                natureza = "DEBITO"
            else:
                natureza = ""

            # Descrição: tudo antes do primeiro valor
            first_val_pos = full.find(values[0])
            desc_raw = full[:first_val_pos].strip() if first_val_pos > 0 else full

            # Separa nº do documento (token numérico inicial)
            documento = ""
            doc_m = re.match(r"^(\d{4,})\s+", desc_raw)
            if doc_m:
                documento = doc_m.group(1)
                desc_raw = desc_raw[doc_m.end():].strip()

            line_idx += 1
            lancamentos.append(LancamentoExtrato(
                linha_origem=line_idx,
                pagina=1,
                data=data,
                descricao_original=desc_raw,
                documento=documento,
                valor=valor,
                natureza_inferida=natureza,
                saldo=saldo,
                linha_original=first_rest or " ".join(extra_lines[:2]),
            ))

        lancamentos.sort(key=lambda x: (x.data or date.max, x.linha_origem))
        return lancamentos


# ──────────────────────────────────────────────────────────────────────────────
# Parser — Banco do Brasil
# Formato: Dt. balancete | Dt. movimento | Ag. | Lote | Histórico | Documento | Valor C/D | Saldo
# O valor aparece como "120,00 C" ou "520,52 D" na mesma célula
# ──────────────────────────────────────────────────────────────────────────────

class BancoBrasilExtratoParser:
    """
    Extrato Conta Corrente do Banco do Brasil.
    pypdf extrai as células em linhas; cada lançamento pode ocupar 2 linhas:
      linha 1: 02/01/2024 0000 14397 821 Pix-Recebido QR Code 4.980.512.658 120,00 C
      linha 2: 30/12 10:53 00077668340220 Lucicleide
    Identificamos pelo padrão: data + lote + código + histórico + doc + valor + C/D
    """

    _DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+(\d{4})\s+(\d+)\s+(\d+)\s+(.+)$")
    _VALUE_DC_RE = re.compile(r"([\d.]+,\d{2})\s+([CD])\s*$")
    _VALUE_DC_INLINE = re.compile(r"([\d.]+,\d{2})\s+([CD])\b")

    def parse(self, uploaded_file) -> ExtratoResult:
        try:
            from pypdf import PdfReader
        except ImportError:
            return ExtratoResult(success=False, erros=["pypdf não instalado."])
        try:
            reader = PdfReader(io.BytesIO(_read_file_bytes(uploaded_file)))
        except Exception as exc:
            return ExtratoResult(success=False, erros=[f"Erro ao ler PDF: {exc}"])

        pages = [p.extract_text() or "" for p in reader.pages]
        full_text = "\n".join(pages)
        lines = [l.strip() for l in full_text.splitlines() if l.strip()]

        header = self._extract_header(full_text)
        lancamentos = self._extract_lancamentos(lines)

        return ExtratoResult(
            success=True,
            header=header,
            lancamentos=lancamentos,
            total_lancamentos=len(lancamentos),
        )

    def _extract_header(self, text: str) -> ExtratoHeader:
        h = ExtratoHeader()
        h.dados_brutos = {"banco": "bb"}

        m = re.search(r"Conta corrente\s+([\w\s\-]+)\n", text, re.IGNORECASE)
        if m:
            h.empresa_nome = m.group(1).strip()

        m = re.search(r"Agência\s+([\d\-]+)", text, re.IGNORECASE)
        if m:
            h.agencia = m.group(1).strip()

        m = re.search(r"Conta corrente\s+([\d\-]+[A-Z]?)", text, re.IGNORECASE)
        if m:
            h.conta = m.group(1).strip()

        m = re.search(r"Per[íi]odo do extrato\s+(\d{2}\s*/\s*\d{4})", text, re.IGNORECASE)
        if m:
            ref = m.group(1).replace(" ", "")
            parts = ref.split("/")
            if len(parts) == 2:
                try:
                    import calendar
                    month, year = int(parts[0]), int(parts[1])
                    h.periodo_inicio = date(year, month, 1)
                    h.periodo_fim = date(year, month, calendar.monthrange(year, month)[1])
                except Exception:
                    pass

        # Saldo anterior (linha "Saldo Anterior ... 0,00 C")
        m = re.search(r"Saldo\s+Anterior\s+([\d.,]+)\s*([CD])", text, re.IGNORECASE)
        if m:
            h.saldo = _parse_brl_decimal(m.group(1))

        return h

    def _extract_lancamentos(self, lines: list[str]) -> list[LancamentoExtrato]:
        lancamentos = []
        line_idx = 0

        _SKIP_RE = re.compile(
            r"^(Dt\.\s+balancete|Lançamentos|Cliente\s*-|Agência|Conta corrente|Per[íi]odo|"
            r"Consultas\s*-|G\d{15}|Saldo\s+Anterior|S\s*A\s*L\s*D\s*O|Transação\s+efetuada"
            r"|Serviço\s+de\s+Atendimento|Para\s+deficientes|Ouvidoria|SAC\s*[0-9])",
            re.IGNORECASE,
        )

        # Agrupa: linha principal + possíveis linhas de complemento
        i = 0
        while i < len(lines):
            line = lines[i]
            if _SKIP_RE.search(line):
                i += 1
                continue

            m = self._DATE_RE.match(line)
            if not m:
                i += 1
                continue

            date_str = m.group(1)
            data = _parse_date_br(date_str)
            if not data:
                i += 1
                continue

            rest = m.group(5).strip()

            # A próxima linha pode ser a continuação (hora e nome do favorecido)
            complemento = ""
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                # Complemento: começa com HH/MM ou com CPF/CNPJ (só dígitos)
                if re.match(r"^\d{2}/\d{2}\s+\d{2}:\d{2}", next_line) or re.match(r"^\d{2}/\d{2}\s+\d{2}:\d{2}", next_line):
                    complemento = next_line
                    i += 1
                elif re.match(r"^\d{11,14}\s+", next_line) or re.match(r"^[A-Z]{2,}", next_line):
                    complemento = next_line
                    i += 1

            # Extrai valor + D/C do final da linha rest
            dc_m = self._VALUE_DC_RE.search(rest)
            if not dc_m:
                dc_m = self._VALUE_DC_INLINE.search(rest)
            if not dc_m:
                i += 1
                continue

            valor_str = dc_m.group(1)
            dc = dc_m.group(2)
            valor = _parse_brl_decimal(valor_str)
            if valor <= 0:
                i += 1
                continue

            natureza = "CREDITO" if dc == "C" else "DEBITO"

            # Histórico: tudo antes do valor, remove o documento (número longo no final)
            # Documentos do BB podem ter pontos: 4.980.512.658 → remove pontos para validar
            before_val = rest[:dc_m.start()].strip()
            documento = ""
            doc_m = re.search(r"((?:\d{1,3}\.)*\d{3,}|\d{7,})\s*$", before_val)
            if doc_m:
                raw_doc = doc_m.group(1)
                clean_doc = raw_doc.replace(".", "")
                if len(clean_doc) >= 7 and clean_doc.isdigit():
                    documento = clean_doc
                    before_val = before_val[:doc_m.start()].strip()

            # Descrição composta
            desc = before_val
            if complemento:
                # Pegar apenas a parte do nome (após hora)
                nome_m = re.search(r"\d{2}:\d{2}\s+\d+\s+(.+)", complemento)
                if nome_m:
                    desc = f"{desc} — {nome_m.group(1).strip()}"

            line_idx += 1
            lancamentos.append(LancamentoExtrato(
                linha_origem=line_idx,
                pagina=1,
                data=data,
                descricao_original=desc.strip(),
                documento=documento,
                valor=valor,
                natureza_inferida=natureza,
                saldo=None,
                linha_original=line,
            ))

            i += 1

        lancamentos.sort(key=lambda x: (x.data or date.max, x.linha_origem))
        return lancamentos


# ──────────────────────────────────────────────────────────────────────────────
# Parser — Santander (Extrato Consolidado Inteligente PJ)
# Formato: Data | Descrição | Nº Documento | Créditos (R$) | Débitos (R$) | Saldo (R$)
# pypdf extrai cada coluna como texto separado por espaços
# ──────────────────────────────────────────────────────────────────────────────

class SantanderExtratoParser:
    """
    Extrato Consolidado Inteligente do Santander Empresas.
    O PDF tem colunas: Data | Descrição | Nº Documento | Créditos | Débitos | Saldo
    pypdf tende a extrair a linha assim:
      02/01 TARIFA MENSALIDADE PACOTE SERVICOS - 106,50- 0,00
      02/01 TED RECEBIDA TRANSFERENCIA ENTRE CONTA - 2.300,00
    Heurística: valor com "-" no final é débito; valor sem "-" é crédito.
    """

    _DATE_RE = re.compile(r"^(\d{2}/\d{2})\s+(.+)$")
    _VALUE_RE = re.compile(r"([\d.]+,\d{2})(-?)")

    def parse(self, uploaded_file) -> ExtratoResult:
        try:
            from pypdf import PdfReader
        except ImportError:
            return ExtratoResult(success=False, erros=["pypdf não instalado."])
        try:
            reader = PdfReader(io.BytesIO(_read_file_bytes(uploaded_file)))
        except Exception as exc:
            return ExtratoResult(success=False, erros=[f"Erro ao ler PDF: {exc}"])

        pages = [p.extract_text() or "" for p in reader.pages]
        full_text = "\n".join(pages)
        lines = [l.strip() for l in full_text.splitlines() if l.strip()]

        header = self._extract_header(full_text)
        lancamentos = self._extract_lancamentos(lines, header.periodo_inicio)

        return ExtratoResult(
            success=True,
            header=header,
            lancamentos=lancamentos,
            total_lancamentos=len(lancamentos),
        )

    def _extract_header(self, text: str) -> ExtratoHeader:
        h = ExtratoHeader()
        h.dados_brutos = {"banco": "santander"}

        m = re.search(r"Nome\s*\n(.+)", text, re.IGNORECASE)
        if m:
            h.empresa_nome = m.group(1).strip()

        m = re.search(r"Agência\s*\n?(\d+)", text, re.IGNORECASE)
        if m:
            h.agencia = m.group(1).strip()

        m = re.search(r"Conta Corrente\s*\n?([\d.]+\-\d)", text, re.IGNORECASE)
        if m:
            h.conta = m.group(1).strip()

        # Período pelo cabeçalho "Resumo - janeiro/2024"
        m = re.search(r"Resumo\s*[-–]\s*(\w+)/(\d{4})", text, re.IGNORECASE)
        if m:
            meses = {
                "janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4,
                "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
                "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
            }
            mes_nome = m.group(1).lower()
            ano = int(m.group(2))
            mes = meses.get(mes_nome)
            if mes:
                import calendar
                h.periodo_inicio = date(ano, mes, 1)
                h.periodo_fim = date(ano, mes, calendar.monthrange(ano, mes)[1])

        # Saldo em 31/xx
        m = re.search(r"Saldo\s+de\s+Conta\s+Corrente\s+em\s+31/\d{2}\s+([\d.,]+)", text, re.IGNORECASE)
        if m:
            h.saldo = _parse_brl_decimal(m.group(1))

        return h

    def _extract_lancamentos(self, lines: list[str], ref_date: date | None) -> list[LancamentoExtrato]:
        lancamentos = []
        line_idx = 0

        _SKIP_RE = re.compile(
            r"^(SALDO\s+EM|Pagina:|Extrato_PJ|BALP_|Prezado|Conhe[çc]a|Fale\s+Conosco"
            r"|Central\s+de|SAC\s*[-–]|Ouvidoria|Redes\s+Sociais|Agência|Conta\s+Corrente"
            r"|Per[íi]odo|Resumo\s*[-–]|Nome\s*$|Data\s+Descri|Movimenta[çc][aã]o"
            r"|Cr[eé]ditos\s+D[eé]bitos|www\.|@|http|EXTRATO|janeiro|fevereiro|mar[çc]o"
            r"|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro"
            r"|Saldos\s+por|D[eé]bito\s+Autom|Comprova|Transfer[êe]ncia|Cr[eé]ditos\s+Contrat"
            r"|Pacote\s+de|Programa\s+de|[ÍI]ndices\s+Econ|Voc[êe]\s+e\s+Seu|A\s+gente\s+est)",
            re.IGNORECASE,
        )

        # Ignora lançamentos internos do ContaMax/BB Rende (são só movimentos de investimento interno)
        _INTERNAL_RE = re.compile(
            r"(APLICA[CÇ][AÃ]O\s+CONTAMAX|RESGATE\s+CONTAMAX|BB\s+RENDE\s+F[AÁ]CIL)",
            re.IGNORECASE,
        )

        year = ref_date.year if ref_date else date.today().year
        month = ref_date.month if ref_date else date.today().month

        in_movimentacao = False

        for line in lines:
            if _SKIP_RE.search(line):
                in_movimentacao = False
                continue

            if re.search(r"Movimenta[çc][aã]o", line, re.IGNORECASE):
                in_movimentacao = True
                continue

            if not in_movimentacao:
                # Tenta detectar início da seção de movimentação por data DD/MM
                if not self._DATE_RE.match(line):
                    continue
                in_movimentacao = True

            m = self._DATE_RE.match(line)
            if not m:
                continue

            date_str = m.group(1)
            rest = m.group(2).strip()

            # Ignora lançamentos internos ContaMax
            if _INTERNAL_RE.search(rest):
                continue

            # Tenta parsear a data com o ano de referência
            try:
                day, mon = map(int, date_str.split("/"))
                data = date(year, mon, day)
            except Exception:
                continue

            # Extrai todos os valores da linha
            values_raw = self._VALUE_RE.findall(rest)  # lista de (valor, sinal)
            if not values_raw:
                continue

            # Remove valores do texto para obter descrição
            desc_part = self._VALUE_RE.sub("", rest).strip()
            desc_part = re.sub(r"\s{2,}", " ", desc_part).strip()

            # Separar documento (número isolado) da descrição
            documento = ""
            doc_m = re.search(r"\b(\d{4,})\s*$", desc_part)
            if doc_m:
                documento = doc_m.group(1)
                desc_part = desc_part[:doc_m.start()].strip()

            # Determina natureza e valor:
            # No Santander: valor débito vem com "-" no final (ex: "106,50-")
            # Valor crédito vem sem sinal (ex: "2.300,00")
            # O último valor pode ser o saldo ("0,00")
            debito_vals = [(v, s) for v, s in values_raw if s == "-"]
            credito_vals = [(v, s) for v, s in values_raw if s == ""]

            # Remove o "0,00" final (saldo) se for o único valor "crédito"
            saldo_val = None
            if credito_vals and _parse_brl_decimal(credito_vals[-1][0]) == Decimal("0"):
                saldo_val = Decimal("0")
                credito_vals = credito_vals[:-1]

            if debito_vals:
                valor = _parse_brl_decimal(debito_vals[0][0])
                natureza = "DEBITO"
            elif credito_vals:
                valor = _parse_brl_decimal(credito_vals[0][0])
                natureza = "CREDITO"
            else:
                # Todos os valores são 0 ou ambíguos
                continue

            if valor <= 0:
                continue

            line_idx += 1
            lancamentos.append(LancamentoExtrato(
                linha_origem=line_idx,
                pagina=1,
                data=data,
                descricao_original=desc_part,
                documento=documento,
                valor=valor,
                natureza_inferida=natureza,
                saldo=saldo_val,
                linha_original=line,
            ))

        lancamentos.sort(key=lambda x: (x.data or date.max, x.linha_origem))
        return lancamentos