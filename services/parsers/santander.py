"""
Parser para extrato Santander Extrato Consolidado Inteligente PJ.
Formato: Data | Descrição | Nº Documento | Créditos (R$) | Débitos (R$) | Saldo (R$)
"""
from __future__ import annotations

import io
import re
from datetime import date
from decimal import Decimal

from .base import (
    ExtratoHeader,
    ExtratoResult,
    LancamentoExtrato,
    _parse_brl_decimal,
    _read_file_bytes,
)


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

        # CNPJ/CPF do titular
        m = re.search(r"CNPJ[:\s]*([\d]{2}[\.\d]{11}[\/]?\d{4}[-]?\d{2})", text, re.IGNORECASE)
        if not m:
            m = re.search(r"\b(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})\b", text)
        if m:
            h.empresa_cnpj = m.group(1).strip()

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
        """
        Máquina de estados para o Santander Extrato Consolidado Inteligente.

        Problema: pypdf extrai cada célula visualmente separada como uma linha distinta.
        Apenas a PRIMEIRA transação de cada data tem o prefixo "DD/MM"; as demais linhas do
        mesmo dia aparecem sem data. Além disso, descrições longas podem vir em múltiplas
        linhas antes da linha com o valor monetário.

        Estratégia:
          1. Rastrear current_date sempre que encontrarmos "DD/MM" no início de uma linha.
          2. Acumular linhas de descrição (sem valor) em pending_desc.
          3. Ao encontrar uma linha com valor monetário, construir o lançamento a partir de
             pending_desc + a descrição presente na linha do valor.
          4. Parar ao detectar início de outra seção do extrato (investimentos, débito
             automático, etc.).
        """
        lancamentos = []
        line_idx = 0

        year = ref_date.year if ref_date else date.today().year

        # Marcadores de fim da seção Conta Corrente → para de processar
        _END_RE = re.compile(
            r"^(D[eé]bito\s+Autom[aá]tico\s+em\s+Conta"
            r"|Saldos\s+por\s+Per[íi]odo"
            r"|Comprovantes?\s+de\s+Pagamento"
            r"|Cr[eé]ditos\s+Contratados"
            r"|Pacote\s+de\s+Servi[çc]os"
            r"|Programa\s+de\s+Relacionamento"
            r"|[ÍI]ndices\s+Econ[ôo]micos"
            r"|Voc[êe]\s+e\s+Seu\s+Dinheiro"
            r"|ContaMax\s+Empresarial"
            r"|Posi[çc][aã]o\s+Consolidada"
            r"|A\s+gente\s+est[aá]\s+aqui)",
            re.IGNORECASE,
        )

        # Linhas que devem ser ignoradas mas NÃO encerram a seção
        _SKIP_RE = re.compile(
            r"^(SALDO\s+EM\b"
            r"|Pagina\s*:|Extrato_PJ|BALP_"
            r"|Prezado|Conhe[çc]a|Fale\s+Conosco"
            r"|Central\s+de\s+Atendimento|SAC\s*[-–]|Ouvidoria"
            r"|Redes\s+Sociais|www\.|http|@"
            r"|N[ºo]\s+Documento|Movimentos\s+\(|Saldo\s+\("
            r"|Cr[eé]ditos\s+D[eé]bitos|Data\s+Descri[çc]"
            r"|EXTRATO\s+CONSOLIDADO"
            r"|Agência\s*$|Conta\s+Corrente\s*$|Nome\s*$"
            r"|Resumo\s*[-–]|Per[íi]odo\s*$|Movimenta[çc][aã]o\s*$)",
            re.IGNORECASE,
        )

        # Lançamentos internos de investimento (ContaMax sweep) — ignorar
        _INTERNAL_RE = re.compile(
            r"(APLICA[CÇ][AÃ]O\s+CONTAMAX|RESGATE\s+CONTAMAX)",
            re.IGNORECASE,
        )

        in_movimentacao = False
        current_date: date | None = None
        pending_desc: list[str] = []

        for line in lines:
            # Fim da seção Conta Corrente → encerra
            if _END_RE.match(line):
                break

            # Linhas de ruído — pular
            if _SKIP_RE.match(line):
                continue

            # Início explícito da seção de movimentação
            if re.match(r"^Movimenta[çc][aã]o\s*$", line, re.IGNORECASE):
                in_movimentacao = True
                continue

            if not in_movimentacao:
                if self._DATE_RE.match(line):
                    in_movimentacao = True
                else:
                    continue

            # Lançamentos internos ContaMax — descartar e limpar bloco pendente
            if _INTERNAL_RE.search(line):
                pending_desc.clear()
                continue

            # Detecta prefixo de data DD/MM e atualiza current_date
            date_m = self._DATE_RE.match(line)
            if date_m:
                try:
                    day, mon = map(int, date_m.group(1).split("/"))
                    current_date = date(year, mon, day)
                except Exception:
                    pass
                rest = date_m.group(2).strip()
            else:
                rest = line

            if not rest:
                continue

            # Verifica se há valores monetários na linha
            values_raw = self._VALUE_RE.findall(rest)

            if not values_raw:
                # Linha de descrição pura — acumula no bloco pendente
                pending_desc.append(rest)
                continue

            # ── Linha com valor ──────────────────────────────────────────
            # Extrai a parte descritiva (sem os valores monetários)
            desc_part = self._VALUE_RE.sub("", rest).strip()
            desc_part = re.sub(r"\s{2,}", " ", desc_part).strip()
            # Remove o "-" isolado no final (coluna Nº Documento sem número)
            desc_part = re.sub(r"\s+-\s*$", "", desc_part).strip()

            # Extrai número de documento do final da descrição
            # Exclui anos (19xx/20xx) que podem aparecer como parte da descrição
            documento = ""
            doc_m = re.search(r"\b(\d{4,})\s*$", desc_part)
            if doc_m and not re.match(r"^(19|20)\d{2}$", doc_m.group(1)):
                documento = doc_m.group(1)
                desc_part = desc_part[:doc_m.start()].strip()
            # Remove pontuação solta no final (ex: "/" de "DEZEMBRO / 2023" após retirar o ano)
            desc_part = re.sub(r"[\s/]+$", "", desc_part).strip()

            # Combina linhas pendentes + descrição desta linha
            full_desc = " ".join(p for p in [*pending_desc, desc_part] if p).strip()
            pending_desc.clear()

            if not full_desc or _INTERNAL_RE.search(full_desc):
                continue

            if current_date is None:
                continue

            # Filtra valores zero (saldo 0,00 das contas ContaMax)
            non_zero = [(v, s) for v, s in values_raw if _parse_brl_decimal(v) != Decimal("0")]
            if not non_zero:
                continue

            # Débito: valor com sufixo "-"; Crédito: sem sufixo
            debito_vals = [(v, s) for v, s in non_zero if s == "-"]
            credito_vals = [(v, s) for v, s in non_zero if s == ""]

            # Usa o PRIMEIRO valor de cada natureza (valores seguintes são saldo corrente)
            if debito_vals:
                valor = _parse_brl_decimal(debito_vals[0][0])
                natureza = "DEBITO"
            elif credito_vals:
                valor = _parse_brl_decimal(credito_vals[0][0])
                natureza = "CREDITO"
            else:
                continue

            if valor <= 0:
                continue

            line_idx += 1
            lancamentos.append(LancamentoExtrato(
                linha_origem=line_idx,
                pagina=1,
                data=current_date,
                descricao_original=full_desc,
                documento=documento,
                valor=valor,
                natureza_inferida=natureza,
                saldo=None,
                linha_original=line,
            ))

        lancamentos.sort(key=lambda x: (x.data or date.max, x.linha_origem))
        return lancamentos
