"""
Motor ConsiPix baseado em Pandas.

Processa as sessoes criadas pelo front em `SessaoConciliacao`, cruzando os
dados ja extraidos pelos parsers especificos, como NFS-e prefeitura e BB.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Any, Iterable, List

import pandas as pd

from app.models import ConfiancaVinculo, SessaoConciliacao, TipoMovimento


NORMALIZACAO_PAGAMENTOS_BANCO = [
    {"como_vem": "PIX", "normalizado": "PIX", "cruza_com": "Banco"},
    {"como_vem": "PAGAMENTO À VISTA", "normalizado": "PAGAMENTO_A_VISTA", "cruza_com": "Banco"},
    {"como_vem": "DEPÓSITO", "normalizado": "DEPOSITO", "cruza_com": "Banco"},
    {"como_vem": "TED", "normalizado": "TED", "cruza_com": "Banco"},
]

NORMALIZACAO_CARTOES = [
    {"como_vem": "CARTÃO DE CRÉDITO / * CRÉDITO", "normalizado": "CARTAO_CREDITO", "cruza_com": "Getnet"},
    {"como_vem": "CARTÃO DE DÉBITO / * DÉBITO / ELECTR / MAESTRO", "normalizado": "CARTAO_DEBITO", "cruza_com": "Getnet/Santander"},
    {"como_vem": "ANTECIPACAOGETNET", "normalizado": "ANTECIPACAO_GETNET", "cruza_com": "Santander"},
]


class MotorConsiPixPandas:
    def __init__(self, escritorio_id: str, empresa_id: str):
        self.escritorio_id = escritorio_id
        self.empresa_id = empresa_id
        self.df_notas: pd.DataFrame = pd.DataFrame()
        self.df_bancos: pd.DataFrame = pd.DataFrame()
        self.df_maquininhas: pd.DataFrame = pd.DataFrame()

    def carregar_dados(self, sessoes_ids: List[str]):
        """
        Carrega os lancamentos das sessoes ConsiPix para DataFrames.

        O front grava `SessaoConciliacao.dados_lancamentos` com o resultado dos
        parsers. Portanto, aqui a chave e a sessao, nao `TransacaoImportada`.
        """
        sessoes = SessaoConciliacao.objects.filter(
            escritorio_id=self.escritorio_id,
            empresa_id=self.empresa_id,
            id__in=sessoes_ids,
        )

        notas = []
        bancos = []
        maquininhas = []

        for sessao in sessoes:
            banco = (sessao.banco or "").lower()
            lancamentos = sessao.dados_lancamentos or []
            if not isinstance(lancamentos, list):
                continue

            is_nfse = self._is_nfse_session(sessao, lancamentos)
            is_getnet = self._is_getnet_session(sessao, lancamentos)
            for index, row in enumerate(lancamentos):
                if not isinstance(row, dict):
                    continue

                if is_getnet:
                    getnet_row = self._build_getnet_row(sessao, row, index)
                    if getnet_row:
                        maquininhas.append(getnet_row)
                    continue

                if is_nfse:
                    nota = self._build_nota_row(sessao, row, index)
                    if nota:
                        notas.append(nota)
                    continue

                banco_row = self._build_banco_row(sessao, row, index, banco)
                if banco_row:
                    bancos.append(banco_row)

        self.df_notas = pd.DataFrame(notas)
        self.df_bancos = pd.DataFrame(bancos)
        self.df_maquininhas = pd.DataFrame(maquininhas)

    def executar_etapa_1(self) -> dict:
        """
        Processo 1: NFS-e nao-cartao vs creditos do Banco do Brasil.
        """
        meios_nao_cartao = {"PIX", "TED", "DEPOSITO", "PAGAMENTO_A_VISTA"}

        notas_etapa = (
            self.df_notas[
                self.df_notas["tipo_pagamento_norm"].isin(meios_nao_cartao)
                & (self.df_notas["status_conciliado"] == False)  # noqa: E712
            ].copy()
            if not self.df_notas.empty
            else pd.DataFrame()
        )

        banco_bb = (
            self.df_bancos[
                self.df_bancos["banco_norm"].str.contains("brasil|bb|banco do brasil", case=False, na=False)
                & (self.df_bancos["tipo_movimento"] == TipoMovimento.CREDITO)
                & (self.df_bancos["status_conciliado"] == False)  # noqa: E712
            ].copy()
            if not self.df_bancos.empty
            else pd.DataFrame()
        )

        if notas_etapa.empty or banco_bb.empty:
            return {
                "sucesso": False,
                "mensagem": (
                    "Dados insuficientes: Encontrei "
                    f"{len(notas_etapa)} notas (PIX/TED/DEP) e {len(banco_bb)} lancamentos no BB."
                ),
                "total_notas": int(len(self.df_notas)),
                "total_banco": int(len(self.df_bancos)),
                "resumo_pix": self._resumo_pix(set(), set()),
                "debug": self._debug_payload(notas_etapa=notas_etapa, banco_bb=banco_bb, matched_nota_ids=set(), matched_banco_ids=set()),
            }

        banco_bb_debug = banco_bb.copy()
        vinculos_criados = 0
        alertas_discrepancia = 0
        detalhes = []
        matched_nota_ids: set[str] = set()
        matched_banco_ids: set[str] = set()

        def registrar_match(nota: pd.Series, match: pd.DataFrame, confianca: str, modo_match: str) -> None:
            nonlocal alertas_discrepancia, banco_bb, vinculos_criados
            diff_dias = max(abs((nota["data_movimento"] - row["data_movimento"]).days) for _, row in match.iterrows())
            alerta_erro = diff_dias > 365
            if alerta_erro:
                alertas_discrepancia += 1

            banco_total = sum((row["valor"] for _, row in match.iterrows()), Decimal("0"))
            banco_descricoes = []
            banco_documentos = []
            banco_datas = []
            banco_cpfs = []
            banco_nomes = []
            banco_valores = []
            for _, banco_row in match.iterrows():
                banco_descricoes.append(str(banco_row["descricao"]))
                banco_documentos.append(str(banco_row["documento"]))
                banco_datas.append(banco_row["data_movimento"].strftime("%d/%m/%Y"))
                banco_valores.append(str(banco_row["valor"]))
                if banco_row.get("banco_cpf_cnpj"):
                    banco_cpfs.append(str(banco_row.get("banco_cpf_cnpj")))
                if banco_row.get("banco_nome_extraido"):
                    banco_nomes.append(str(banco_row.get("banco_nome_extraido")))

            cpf_match = bool(nota.get("cpf_tomador_digits") and nota.get("cpf_tomador_digits") in set(banco_cpfs))
            detalhes.append(
                {
                    "nota_numero": nota["numero_nota"],
                    "nota_tomador": nota["tomador"],
                    "nota_data": nota["data_movimento"].strftime("%d/%m/%Y"),
                    "nota_valor": float(nota["valor"]),
                    "nota_tipo_pagamento": nota["tipo_pagamento"],
                    "nota_cpf_tomador": nota.get("cpf_tomador_digits", ""),
                    "banco_data": ", ".join(banco_datas),
                    "banco_valor": float(banco_total),
                    "banco_descricao": " | ".join(banco_descricoes),
                    "banco_documento": " | ".join(banco_documentos),
                    "banco_cpf_cnpj": ", ".join(sorted(set(banco_cpfs))),
                    "banco_nome_extraido": ", ".join(sorted(set(banco_nomes))),
                    "banco_qtd_lancamentos": int(len(match)),
                    "banco_valores": ", ".join(banco_valores),
                    "match_tipo": modo_match,
                    "cpf_match": cpf_match,
                    "confianca": confianca,
                    "alerta_erro": alerta_erro,
                    "diferenca_dias": diff_dias,
                }
            )

            matched_nota_ids.add(str(nota["id"]))
            for target_idx, target_banco in match.iterrows():
                ids_duplicados = self._ids_banco_duplicados(target_banco)
                matched_banco_ids.update(ids_duplicados)
                banco_bb = banco_bb[~banco_bb["id"].astype(str).isin(ids_duplicados)]
            vinculos_criados += 1

        notas_pendentes = notas_etapa.copy()
        for exigir_cpf in (True, False):
            notas_processadas = []
            for nota_idx, nota in notas_pendentes.iterrows():
                if exigir_cpf and not nota.get("cpf_tomador_digits"):
                    continue

                match, confianca, modo_match = self._buscar_match_banco(nota, banco_bb, exigir_cpf=exigir_cpf)
                if match.empty:
                    continue

                registrar_match(nota, match, confianca, modo_match)
                notas_processadas.append(nota_idx)

            if notas_processadas:
                notas_pendentes = notas_pendentes.drop(index=notas_processadas)

        return {
            "sucesso": True,
            "vinculos_criados": vinculos_criados,
            "alertas_discrepancia": alertas_discrepancia,
            "total_notas": int(len(notas_etapa)),
            "total_banco": int(len(self.df_bancos)),
            "detalhes": detalhes,
            "resumo_pix": self._resumo_pix(matched_nota_ids, matched_banco_ids),
            "debug": self._debug_payload(
                notas_etapa=notas_etapa,
                banco_bb=banco_bb_debug,
                matched_nota_ids=matched_nota_ids,
                matched_banco_ids=matched_banco_ids,
            ),
        }

    def executar_etapa_2(self) -> dict:
        """
        Processo 2: NFS-e cartao vs Getnet vs creditos Santander.
        """
        tipos_cartao = {"CARTAO_CREDITO", "CARTAO_DEBITO"}
        notas_cartao = (
            self.df_notas[
                self.df_notas["tipo_pagamento_norm"].isin(tipos_cartao)
                & (self.df_notas["status_conciliado"] == False)  # noqa: E712
            ].copy()
            if not self.df_notas.empty
            else pd.DataFrame()
        )
        getnet = (
            self.df_maquininhas[
                self.df_maquininhas["adquirente_norm"].str.contains("GETNET", case=False, na=False)
                & self.df_maquininhas["tipo_cartao_norm"].isin(tipos_cartao)
            ].copy()
            if not self.df_maquininhas.empty
            else pd.DataFrame()
        )
        santander = (
            self.df_bancos[
                self.df_bancos["banco_norm"].str.contains("SANTANDER", case=False, na=False)
                & (self.df_bancos["tipo_movimento"] == TipoMovimento.CREDITO)
                & (self.df_bancos["status_conciliado"] == False)  # noqa: E712
            ].copy()
            if not self.df_bancos.empty
            else pd.DataFrame()
        )
        santander_cartao = self._filter_santander_cartao(santander)

        if getnet.empty or santander_cartao.empty:
            return {
                "sucesso": False,
                "mensagem": (
                    "Dados insuficientes: Encontrei "
                    f"{len(notas_cartao)} notas cartao, {len(getnet)} linhas Getnet e "
                    f"{len(santander_cartao)} creditos Santander de cartao/Getnet."
                ),
                "total_notas": int(len(notas_cartao)),
                "total_banco": int(len(self.df_bancos)),
                "resumo_cartao": self._resumo_cartao(notas_cartao, getnet, santander_cartao, set(), set()),
                "debug": self._debug_payload_cartao(notas_cartao, getnet, santander_cartao, set(), set()),
            }

        notas_disponiveis = notas_cartao.copy()
        santander_disponivel = santander_cartao.copy()
        matched_getnet_ids: set[str] = set()
        matched_santander_ids: set[str] = set()
        detalhes = []
        antecipacoes = self._count_notas_cartao_antecipadas(notas_cartao)
        diferenca_total = Decimal("0")
        getnet_totais = self._totais_getnet_por_data_tipo(getnet)

        for _, venda in getnet.sort_values(["data_movimento", "tipo_cartao_norm", "valor_liquido"]).iterrows():
            match_banco, confianca, match_tipo = self._buscar_match_santander_getnet(venda, santander_disponivel)
            nota_grupo, nota_indices = self._buscar_notas_para_getnet(venda, notas_disponiveis)
            getnet_grupo_bruto = getnet_totais.get((venda["data_movimento"], venda["tipo_cartao_norm"]), venda["valor_bruto"])
            antecipado = self._is_antecipacao_nota(nota_grupo)

            if match_banco.empty:
                detalhes.append(
                    self._detalhe_cartao(
                        venda,
                        nota_grupo,
                        None,
                        ConfiancaVinculo.BAIXA,
                        "SEM_SANTANDER",
                        getnet_grupo_bruto=getnet_grupo_bruto,
                        antecipado=antecipado,
                    )
                )
                continue

            banco_row = match_banco.iloc[0]
            diferenca_antecipacao = Decimal("0")
            if antecipado:
                diferenca_antecipacao = (venda["valor_liquido"] - banco_row["valor"]).copy_abs()
                diferenca_total += diferenca_antecipacao

            detalhes.append(
                self._detalhe_cartao(
                    venda,
                    nota_grupo,
                    banco_row,
                    confianca,
                    match_tipo,
                    getnet_grupo_bruto=getnet_grupo_bruto,
                    antecipado=antecipado,
                    diferenca_antecipacao=diferenca_antecipacao,
                )
            )
            matched_getnet_ids.add(str(venda["id"]))
            matched_santander_ids.add(str(banco_row["id"]))
            santander_disponivel = santander_disponivel.drop(match_banco.index[0])
            if nota_indices:
                notas_disponiveis = notas_disponiveis.drop(index=nota_indices, errors="ignore")

        return {
            "sucesso": True,
            "vinculos_criados": int(len(matched_getnet_ids)),
            "alertas_discrepancia": int(antecipacoes),
            "total_notas": int(len(notas_cartao)),
            "total_banco": int(len(self.df_bancos)),
            "detalhes": detalhes,
            "resumo_cartao": self._resumo_cartao(notas_cartao, getnet, santander_cartao, matched_getnet_ids, matched_santander_ids),
            "debug": self._debug_payload_cartao(notas_cartao, getnet, santander_cartao, matched_getnet_ids, matched_santander_ids),
            "antecipacoes": antecipacoes,
            "diferenca_total_antecipacao": str(diferenca_total),
        }

    def _count_notas_cartao_antecipadas(self, notas_cartao: pd.DataFrame) -> int:
        if notas_cartao.empty or "data_emissao" not in notas_cartao.columns:
            return 0
        return int(
            sum(
                1
                for _, row in notas_cartao.iterrows()
                if row.get("data_emissao") and row.get("data_movimento") and row["data_movimento"] < row["data_emissao"]
            )
        )

    def _buscar_notas_para_getnet(self, venda: pd.Series, notas_disponiveis: pd.DataFrame) -> tuple[pd.Series | None, list[Any]]:
        if notas_disponiveis.empty:
            return None, []

        candidatos = notas_disponiveis[
            (notas_disponiveis["data_movimento"] == venda["data_movimento"])
            & (notas_disponiveis["tipo_pagamento_norm"] == venda["tipo_cartao_norm"])
        ].copy()
        if candidatos.empty:
            candidatos = notas_disponiveis[
                (notas_disponiveis["data_movimento"] == venda["data_movimento"])
                & (notas_disponiveis["valor"] == venda["valor_bruto"])
            ].copy()
        if candidatos.empty:
            return None, []
        candidatos = self._filtrar_notas_por_bandeira_getnet(candidatos, venda)
        if candidatos.empty:
            return None, []

        exact = candidatos[candidatos["valor"] == venda["valor_bruto"]]
        if not exact.empty:
            exact = exact.sort_values(["numero_nota", "tomador"])
            return self._resumir_notas_cartao(exact.head(1)), list(exact.head(1).index)

        indices = list(candidatos.sort_values(["valor", "numero_nota"], ascending=[False, True]).head(8).index)
        for tamanho in range(2, min(5, len(indices)) + 1):
            for combo in combinations(indices, tamanho):
                soma = sum((candidatos.loc[idx, "valor"] for idx in combo), Decimal("0"))
                if soma == venda["valor_bruto"]:
                    combo_df = candidatos.loc[list(combo)]
                    return self._resumir_notas_cartao(combo_df), list(combo)

        candidatos["_valor_diff"] = candidatos["valor"].apply(lambda value: (value - venda["valor_bruto"]).copy_abs())
        fallback = candidatos.sort_values(["_valor_diff", "numero_nota"]).head(1).drop(columns=["_valor_diff"])
        return self._resumir_notas_cartao(fallback), []

    def _filtrar_notas_por_bandeira_getnet(self, candidatos: pd.DataFrame, venda: pd.Series) -> pd.DataFrame:
        if candidatos.empty or "bandeira_cartao_norm" not in candidatos.columns:
            return candidatos

        bandeira_getnet = str(venda.get("bandeira_cartao_norm") or "")
        if not bandeira_getnet:
            return candidatos

        bandeiras_notas = candidatos["bandeira_cartao_norm"].fillna("").astype(str)
        compativeis = candidatos[(bandeiras_notas == "") | (bandeiras_notas == bandeira_getnet)].copy()
        return compativeis

    def _resumir_notas_cartao(self, grupo: pd.DataFrame) -> pd.Series:
        antecipadas = [
            str(row["numero_nota"])
            for _, row in grupo.iterrows()
            if row.get("data_emissao") and row.get("data_movimento") and row["data_movimento"] < row["data_emissao"]
        ]
        datas_emissao = sorted(
            {
                row["data_emissao"]
                for _, row in grupo.iterrows()
                if row.get("data_emissao")
            }
        )
        antecipado_nota = len(antecipadas) > 0
        bandeiras = sorted({str(value) for value in grupo.get("bandeira_cartao_norm", pd.Series(dtype=str)).tolist() if str(value)})
        return pd.Series(
            {
                "data_movimento": grupo["data_movimento"].iloc[0],
                "tipo_cartao_norm": grupo["tipo_pagamento_norm"].iloc[0],
                "bandeira_cartao_norm": ", ".join(bandeiras),
                "valor": sum((row["valor"] for _, row in grupo.iterrows()), Decimal("0")),
                "qtd_notas": int(len(grupo)),
                "numero_notas": ", ".join(str(value) for value in grupo["numero_nota"].tolist() if str(value)),
                "tomadores": " | ".join(str(value) for value in grupo["tomador"].head(5).tolist() if str(value)),
                "data_emissao_min": datas_emissao[0] if datas_emissao else None,
                "data_emissao_resumo": ", ".join(data_emissao.isoformat() for data_emissao in datas_emissao),
                "antecipado_nota": antecipado_nota,
                "antecipadas_qtd": len(antecipadas),
                "antecipadas_notas": ", ".join(antecipadas),
            }
        )

    def _totais_getnet_por_data_tipo(self, getnet: pd.DataFrame) -> dict[tuple[date, str], Decimal]:
        totais: dict[tuple[date, str], Decimal] = {}
        if getnet.empty:
            return totais

        for (data_movimento, tipo_cartao), grupo in getnet.groupby(["data_movimento", "tipo_cartao_norm"]):
            totais[(data_movimento, tipo_cartao)] = sum((row["valor_bruto"] for _, row in grupo.iterrows()), Decimal("0"))
        return totais

    def _buscar_match_santander_getnet(self, venda: pd.Series, santander: pd.DataFrame) -> tuple[pd.DataFrame, str, str]:
        if santander.empty:
            return pd.DataFrame(), ConfiancaVinculo.BAIXA, "SEM_SANTANDER"

        mesma_data = santander[santander["data_movimento"] == venda["data_movimento"]]
        match = mesma_data[mesma_data["valor"] == venda["valor_liquido"]]
        match = self._filtrar_banco_por_bandeira_getnet(match, venda)
        if not match.empty:
            return match.head(1), ConfiancaVinculo.ALTA, "GETNET_LIQUIDO_MESMA_DATA"

        janela = santander[
            (santander["data_movimento"] >= venda["data_movimento"] - timedelta(days=7))
            & (santander["data_movimento"] <= venda["data_movimento"] + timedelta(days=7))
            & (santander["valor"] == venda["valor_liquido"])
        ].copy()
        janela = self._filtrar_banco_por_bandeira_getnet(janela, venda)
        if not janela.empty:
            janela["_diff_dias"] = janela["data_movimento"].apply(lambda value: abs((venda["data_movimento"] - value).days))
            return janela.sort_values(["_diff_dias"]).drop(columns=["_diff_dias"]).head(1), ConfiancaVinculo.MEDIA, "GETNET_LIQUIDO_JANELA_7_DIAS"

        valor_minimo_parcial = venda["valor_liquido"] * Decimal("0.70")
        antecipacao = santander[
            (santander["descricao_norm"].str.contains("ANTECIPACAOGETNET|ANTECIPACAO GETNET", na=False))
            & (santander["data_movimento"] >= venda["data_movimento"] - timedelta(days=7))
            & (santander["data_movimento"] <= venda["data_movimento"] + timedelta(days=7))
            & (santander["valor"] <= venda["valor_liquido"])
            & (santander["valor"] >= valor_minimo_parcial)
        ].copy()
        antecipacao = self._filtrar_banco_por_bandeira_getnet(antecipacao, venda)
        if not antecipacao.empty:
            antecipacao["_diff_valor"] = antecipacao["valor"].apply(lambda value: (venda["valor_liquido"] - value).copy_abs())
            antecipacao["_diff_dias"] = antecipacao["data_movimento"].apply(lambda value: abs((venda["data_movimento"] - value).days))
            return antecipacao.sort_values(["_diff_dias", "_diff_valor"]).drop(columns=["_diff_valor", "_diff_dias"]).head(1), ConfiancaVinculo.MEDIA, "SANTANDER_MENOR_JANELA_7_DIAS"

        return pd.DataFrame(), ConfiancaVinculo.BAIXA, "SEM_SANTANDER"

    def _filtrar_banco_por_bandeira_getnet(self, candidatos: pd.DataFrame, venda: pd.Series) -> pd.DataFrame:
        if candidatos.empty or "bandeira_cartao_norm" not in candidatos.columns:
            return candidatos

        bandeira_getnet = str(venda.get("bandeira_cartao_norm") or "")
        if not bandeira_getnet:
            return candidatos

        bandeiras_banco = candidatos["bandeira_cartao_norm"].fillna("").astype(str)
        return candidatos[(bandeiras_banco == "") | (bandeiras_banco == bandeira_getnet)].copy()

    def _detalhe_cartao(
        self,
        venda: pd.Series,
        nota_grupo: pd.Series | None,
        banco_row: pd.Series | None,
        confianca: str,
        match_tipo: str,
        getnet_grupo_bruto: Decimal | None = None,
        antecipado: bool = False,
        diferenca_antecipacao: Decimal | None = None,
    ) -> dict[str, Any]:
        diferenca_antecipacao = diferenca_antecipacao or Decimal("0")
        nota_valor = nota_grupo["valor"] if nota_grupo is not None else Decimal("0")
        getnet_grupo_bruto = getnet_grupo_bruto or venda["valor_bruto"]
        nota_diff = (nota_valor - getnet_grupo_bruto).copy_abs() if nota_grupo is not None else Decimal("0")
        banco_valor = banco_row["valor"] if banco_row is not None else Decimal("0")
        banco_data = banco_row["data_movimento"].strftime("%d/%m/%Y") if banco_row is not None else ""
        banco_descricao = str(banco_row["descricao"]) if banco_row is not None else ""
        return {
            "nota_numero": str(nota_grupo["numero_notas"]) if nota_grupo is not None else "",
            "nota_tomador": str(nota_grupo["tomadores"]) if nota_grupo is not None else "",
            "nota_data": venda["data_movimento"].strftime("%d/%m/%Y"),
            "nota_data_emissao": str(nota_grupo.get("data_emissao_resumo") or "") if nota_grupo is not None else "",
            "nota_valor": float(nota_valor),
            "nota_tipo_pagamento": str(venda["tipo_cartao_norm"]),
            "nota_bandeira_cartao": str(nota_grupo.get("bandeira_cartao_norm") or "") if nota_grupo is not None else "",
            "nota_qtd": int(nota_grupo.get("qtd_notas") or 0) if nota_grupo is not None else 0,
            "nota_antecipadas_qtd": int(nota_grupo.get("antecipadas_qtd") or 0) if nota_grupo is not None else 0,
            "nota_antecipadas_notas": str(nota_grupo.get("antecipadas_notas") or "") if nota_grupo is not None else "",
            "banco_data": banco_data,
            "banco_valor": float(banco_valor),
            "banco_descricao": banco_descricao,
            "banco_documento": str(banco_row["documento"]) if banco_row is not None else "",
            "banco_bandeira_cartao": str(banco_row.get("bandeira_cartao_norm") or "") if banco_row is not None else "",
            "banco_qtd_lancamentos": 1 if banco_row is not None else 0,
            "banco_valores": str(banco_valor) if banco_row is not None else "",
            "match_tipo": match_tipo,
            "cpf_match": False,
            "confianca": confianca,
            "alerta_erro": antecipado or bool(nota_diff > Decimal("0.01")),
            "diferenca_dias": abs((venda["data_movimento"] - banco_row["data_movimento"]).days) if banco_row is not None else None,
            "getnet_cartao": str(venda["cartao"]),
            "getnet_data": venda["data_movimento"].isoformat(),
            "getnet_tipo_cartao": str(venda["tipo_cartao_norm"]),
            "getnet_bandeira_cartao": str(venda.get("bandeira_cartao_norm") or ""),
            "getnet_quantidade": int(venda.get("quantidade") or 0),
            "getnet_valor_bruto": float(venda["valor_bruto"]),
            "getnet_grupo_valor_bruto": float(getnet_grupo_bruto),
            "getnet_valor_tarifa": float(venda["valor_tarifa"]),
            "getnet_valor_liquido": float(venda["valor_liquido"]),
            "nota_getnet_diferenca": float(nota_diff),
            "antecipado": antecipado,
            "diferenca_antecipacao": float(diferenca_antecipacao),
        }

    def _resumo_cartao(
        self,
        notas_cartao: pd.DataFrame,
        getnet: pd.DataFrame,
        santander_cartao: pd.DataFrame,
        matched_getnet_ids: set[str],
        matched_santander_ids: set[str],
    ) -> dict[str, Any]:
        getnet_batidas = self._filter_ids(getnet, matched_getnet_ids)
        santander_batidos = self._filter_ids(santander_cartao, matched_santander_ids)
        return {
            "notas_cartao_total": int(len(notas_cartao)),
            "getnet_total": int(len(getnet)),
            "getnet_batidas": int(len(getnet_batidas)),
            "getnet_nao_batidas": int(len(getnet) - len(getnet_batidas)),
            "santander_cartao_creditos": int(len(santander_cartao)),
            "santander_cartao_batidos": int(len(santander_batidos)),
            "santander_cartao_nao_batidos": int(len(santander_cartao) - len(santander_batidos)),
            "valor_bruto_getnet": str(sum((row["valor_bruto"] for _, row in getnet.iterrows()), Decimal("0")) if not getnet.empty else Decimal("0")),
            "valor_liquido_getnet": str(sum((row["valor_liquido"] for _, row in getnet.iterrows()), Decimal("0")) if not getnet.empty else Decimal("0")),
            "tarifa_total_getnet": str(sum((row["valor_tarifa"] for _, row in getnet.iterrows()), Decimal("0")) if not getnet.empty else Decimal("0")),
            "valor_santander_batido": str(sum((row["valor"] for _, row in santander_batidos.iterrows()), Decimal("0")) if not santander_batidos.empty else Decimal("0")),
        }

    def _debug_payload_cartao(
        self,
        notas_cartao: pd.DataFrame,
        getnet: pd.DataFrame,
        santander_cartao: pd.DataFrame,
        matched_getnet_ids: set[str],
        matched_santander_ids: set[str],
    ) -> dict[str, Any]:
        getnet_batidas = self._filter_ids(getnet, matched_getnet_ids)
        getnet_nao_batidas = self._exclude_ids(getnet, matched_getnet_ids)
        santander_batidos = self._filter_ids(santander_cartao, matched_santander_ids)
        santander_nao_batidos = self._exclude_ids(santander_cartao, matched_santander_ids)
        return {
            "normalizacao_pagamentos": NORMALIZACAO_CARTOES,
            "criterios": [
                "Ordem do processo 2: Notas NFS-e, Getnet, Santander",
                "Notas de cartao conferem o valor bruto declarado contra a Getnet por data/tipo/bandeira quando a bandeira existir",
                "Bandeiras aceitas: Mastercard/Maestro, Visa/Visa Electron, Elo, Amex e Hipercard",
                "Antecipacao: nota de cartao com data_pagamento anterior a data_emissao",
                "Getnet cruza com Santander pelo valor liquido e respeita a bandeira quando ela aparecer no historico",
                "Diferenca de antecipacao = valor liquido Getnet - valor creditado no Santander",
            ],
            "notas_df": self._df_to_debug_table(self.df_notas, self._nota_debug_columns()),
            "notas_cartao_df": self._df_to_debug_table(notas_cartao, self._nota_debug_columns()),
            "getnet_df": self._df_to_debug_table(getnet, self._getnet_debug_columns()),
            "getnet_batidas_df": self._df_to_debug_table(getnet_batidas, self._getnet_debug_columns()),
            "getnet_nao_batidas_df": self._df_to_debug_table(getnet_nao_batidas, self._getnet_debug_columns()),
            "santander_cartao_df": self._df_to_debug_table(santander_cartao, self._banco_debug_columns()),
            "santander_cartao_batidos_df": self._df_to_debug_table(santander_batidos, self._banco_debug_columns()),
            "santander_cartao_nao_batidos_df": self._df_to_debug_table(santander_nao_batidos, self._banco_debug_columns()),
            "banco_df": self._df_to_debug_table(self.df_bancos, self._banco_debug_columns()),
        }

    def _resumo_pix(self, matched_nota_ids: set[str], matched_banco_ids: set[str]) -> dict[str, int]:
        notas_pix = self._filter_notas_pix(self.df_notas)
        banco_pix = self._filter_banco_pix(self.df_bancos)
        banco_pix_creditos = banco_pix[banco_pix["tipo_movimento"] == TipoMovimento.CREDITO] if not banco_pix.empty else pd.DataFrame()
        banco_pix_debitos = banco_pix[banco_pix["tipo_movimento"] == TipoMovimento.DEBITO] if not banco_pix.empty else pd.DataFrame()

        notas_batidas = self._filter_ids(notas_pix, matched_nota_ids)
        banco_batidos = self._filter_ids(banco_pix_creditos, matched_banco_ids)

        return {
            "notas_pix_total": int(len(notas_pix)),
            "notas_pix_batidas": int(len(notas_batidas)),
            "notas_pix_nao_batidas": int(len(notas_pix) - len(notas_batidas)),
            "banco_pix_total": int(len(banco_pix)),
            "banco_pix_creditos": int(len(banco_pix_creditos)),
            "banco_pix_debitos": int(len(banco_pix_debitos)),
            "banco_pix_creditos_batidos": int(len(banco_batidos)),
            "banco_pix_creditos_nao_batidos": int(len(banco_pix_creditos) - len(banco_batidos)),
        }

    def _debug_payload(
        self,
        notas_etapa: pd.DataFrame | None = None,
        banco_bb: pd.DataFrame | None = None,
        matched_nota_ids: set[str] | None = None,
        matched_banco_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        matched_nota_ids = matched_nota_ids or set()
        matched_banco_ids = matched_banco_ids or set()
        notas_pix = self._filter_notas_pix(self.df_notas)
        notas_pix_batidas = self._filter_ids(notas_pix, matched_nota_ids)
        notas_pix_nao_batidas = self._exclude_ids(notas_pix, matched_nota_ids)
        banco_pix = self._filter_banco_pix(self.df_bancos)
        banco_pix_creditos = banco_pix[banco_pix["tipo_movimento"] == TipoMovimento.CREDITO] if not banco_pix.empty else pd.DataFrame()
        banco_pix_debitos = banco_pix[banco_pix["tipo_movimento"] == TipoMovimento.DEBITO] if not banco_pix.empty else pd.DataFrame()
        banco_pix_creditos_batidos = self._filter_ids(banco_pix_creditos, matched_banco_ids)
        banco_pix_creditos_nao_batidos = self._exclude_ids(banco_pix_creditos, matched_banco_ids)
        return {
            "normalizacao_pagamentos": NORMALIZACAO_PAGAMENTOS_BANCO,
            "criterios": [
                "Notas: tipo_pagamento normalizado em PIX, TED, DEPOSITO ou PAGAMENTO_A_VISTA",
                "Banco: PIX pode vir em CREDITO ou DEBITO; para receber NFS-e o cruzamento usa PIX CREDITO",
                "Ordem: primeiro processa matches com CPF/CNPJ confirmado; depois tenta os demais por valor/data",
                "Match ALTA: mesmo valor e mesma data; se houver CPF/CNPJ nos dois lados, ele desempata o candidato",
                "Match ALTA AGRUPADO: soma 2 a 5 PIX creditos do mesmo CPF e mesma data quando o total bate exatamente com a nota",
                "Match MEDIA AGRUPADO: soma 2 a 5 PIX creditos do mesmo CPF em janela de 1 dia quando o total bate exatamente com a nota",
                "Match MEDIA: mesmo valor em janela de 15 dias",
                "Match BAIXA: mesmo valor em janela de 730 dias",
            ],
            "notas_df": self._df_to_debug_table(self.df_notas, self._nota_debug_columns()),
            "notas_aptas_df": self._df_to_debug_table(
                notas_etapa if notas_etapa is not None else pd.DataFrame(),
                self._nota_debug_columns(),
            ),
            "notas_pix_df": self._df_to_debug_table(notas_pix, self._nota_debug_columns()),
            "notas_pix_batidas_df": self._df_to_debug_table(notas_pix_batidas, self._nota_debug_columns()),
            "notas_pix_nao_batidas_df": self._df_to_debug_table(notas_pix_nao_batidas, self._nota_debug_columns()),
            "banco_df": self._df_to_debug_table(self.df_bancos, self._banco_debug_columns()),
            "banco_pix_df": self._df_to_debug_table(banco_pix, self._banco_debug_columns()),
            "banco_pix_creditos_df": self._df_to_debug_table(banco_pix_creditos, self._banco_debug_columns()),
            "banco_pix_debitos_df": self._df_to_debug_table(banco_pix_debitos, self._banco_debug_columns()),
            "banco_pix_creditos_batidos_df": self._df_to_debug_table(banco_pix_creditos_batidos, self._banco_debug_columns()),
            "banco_pix_creditos_nao_batidos_df": self._df_to_debug_table(banco_pix_creditos_nao_batidos, self._banco_debug_columns()),
            "banco_bb_creditos_df": self._df_to_debug_table(banco_bb if banco_bb is not None else pd.DataFrame(), self._banco_debug_columns()),
        }

    def _nota_debug_columns(self) -> list[str]:
        return [
            "arquivo_nome",
            "numero_nota",
            "tomador",
            "cpf_tomador",
            "cpf_tomador_digits",
            "data_emissao",
            "data_movimento",
            "valor",
            "tipo_pagamento",
            "tipo_pagamento_norm",
            "bandeira_cartao_norm",
        ]

    def _banco_debug_columns(self) -> list[str]:
        return [
            "arquivo_nome",
            "banco",
            "data_movimento",
            "valor",
            "tipo_movimento",
            "banco_eh_pix",
            "documento",
            "banco_cpf_cnpj",
            "banco_nome_extraido",
            "bandeira_cartao_norm",
            "descricao",
        ]

    def _getnet_debug_columns(self) -> list[str]:
        return [
            "arquivo_nome",
            "adquirente",
            "data_movimento",
            "cartao",
            "tipo_cartao_norm",
            "bandeira_cartao_norm",
            "quantidade",
            "valor_bruto",
            "valor_tarifa",
            "valor_liquido",
            "codigo_estabelecimento",
        ]

    def _filter_santander_cartao(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or "descricao_norm" not in df.columns:
            return pd.DataFrame()
        data = df.copy()
        data["_descricao_compact"] = data["descricao_norm"].astype(str).str.replace(" ", "", regex=False)
        pattern = "GETNET|CARTAODEDEBITO|CARTAODECREDITO|ANTECIPACAOGETNET"
        filtered = data[data["_descricao_compact"].str.contains(pattern, na=False)].copy()
        return filtered.drop(columns=["_descricao_compact"])

    def _filter_notas_pix(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or "tipo_pagamento_norm" not in df.columns:
            return pd.DataFrame()
        return df[df["tipo_pagamento_norm"] == "PIX"].copy()

    def _filter_banco_pix(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or "banco_eh_pix" not in df.columns:
            return pd.DataFrame()
        return df[df["banco_eh_pix"] == True].copy()  # noqa: E712

    def _filter_ids(self, df: pd.DataFrame, ids: set[str]) -> pd.DataFrame:
        if df.empty or "id" not in df.columns or not ids:
            return df.iloc[0:0].copy() if not df.empty else pd.DataFrame()
        return df[df["id"].astype(str).isin(ids)].copy()

    def _exclude_ids(self, df: pd.DataFrame, ids: set[str]) -> pd.DataFrame:
        if df.empty or "id" not in df.columns:
            return pd.DataFrame()
        if not ids:
            return df.copy()
        return df[~df["id"].astype(str).isin(ids)].copy()

    def _ids_banco_duplicados(self, banco_row: pd.Series) -> set[str]:
        if self.df_bancos.empty or "id" not in self.df_bancos.columns:
            return {str(banco_row["id"])}

        duplicados = self.df_bancos
        for column in [
            "arquivo_nome",
            "banco",
            "data_movimento",
            "valor",
            "tipo_movimento",
            "documento",
            "banco_cpf_cnpj",
            "descricao",
        ]:
            if column not in duplicados.columns or column not in banco_row:
                return {str(banco_row["id"])}
            duplicados = duplicados[duplicados[column] == banco_row[column]]

        return set(duplicados["id"].astype(str))

    def _df_to_debug_table(self, df: pd.DataFrame, columns: list[str]) -> dict[str, Any]:
        visible_columns = [column for column in columns if column in df.columns]
        if df.empty or not visible_columns:
            return {"total": 0, "colunas": visible_columns, "linhas": []}

        data = df[visible_columns].copy()
        linhas = []
        for row in data.to_dict(orient="records"):
            linhas.append({key: self._json_value(value) for key, value in row.items()})
        return {"total": int(len(df)), "colunas": visible_columns, "linhas": linhas}

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, date):
            return value.isoformat()
        if pd.isna(value):
            return None
        return value

    def _buscar_match_banco(self, nota: pd.Series, banco_bb: pd.DataFrame, exigir_cpf: bool = False) -> tuple[pd.DataFrame, str, str]:
        match = banco_bb[
            (banco_bb["valor"] == nota["valor"])
            & (banco_bb["data_movimento"] == nota["data_movimento"])
        ]
        if not match.empty:
            priorizado = self._priorizar_match_por_documento(nota, match, exigir_cpf=exigir_cpf)
            if not priorizado.empty:
                return priorizado, ConfiancaVinculo.ALTA, "UNICO_MESMA_DATA"

        agrupado = self._buscar_match_banco_agrupado(nota, banco_bb, timedelta(days=0))
        if not agrupado.empty:
            return agrupado, ConfiancaVinculo.ALTA, "AGRUPADO_MESMO_CPF_MESMA_DATA"

        agrupado = self._buscar_match_banco_agrupado(nota, banco_bb, timedelta(days=1))
        if not agrupado.empty:
            return agrupado, ConfiancaVinculo.MEDIA, "AGRUPADO_MESMO_CPF_JANELA_1_DIA"

        janela_inicio = nota["data_movimento"] - timedelta(days=15)
        janela_fim = nota["data_movimento"] + timedelta(days=15)
        match = banco_bb[
            (banco_bb["valor"] == nota["valor"])
            & (banco_bb["data_movimento"] >= janela_inicio)
            & (banco_bb["data_movimento"] <= janela_fim)
        ]
        if not match.empty:
            priorizado = self._priorizar_match_por_documento(nota, match, exigir_cpf=exigir_cpf)
            if not priorizado.empty:
                return priorizado, ConfiancaVinculo.MEDIA, "UNICO_JANELA_15_DIAS"

        janela_larga_inicio = nota["data_movimento"] - timedelta(days=730)
        janela_larga_fim = nota["data_movimento"] + timedelta(days=730)
        match = banco_bb[
            (banco_bb["valor"] == nota["valor"])
            & (banco_bb["data_movimento"] >= janela_larga_inicio)
            & (banco_bb["data_movimento"] <= janela_larga_fim)
        ]
        if not match.empty:
            priorizado = self._priorizar_match_por_documento(nota, match, exigir_cpf=exigir_cpf)
            if not priorizado.empty:
                return priorizado, ConfiancaVinculo.BAIXA, "UNICO_JANELA_730_DIAS"
        return pd.DataFrame(), ConfiancaVinculo.BAIXA, "SEM_MATCH"

    def _buscar_match_banco_agrupado(self, nota: pd.Series, banco_bb: pd.DataFrame, tolerancia: timedelta) -> pd.DataFrame:
        nota_doc = str(nota.get("cpf_tomador_digits") or "")
        if not nota_doc or nota.get("tipo_pagamento_norm") != "PIX":
            return pd.DataFrame()

        inicio = nota["data_movimento"] - tolerancia
        fim = nota["data_movimento"] + tolerancia
        candidatos = banco_bb[
            (banco_bb["banco_eh_pix"] == True)  # noqa: E712
            & (banco_bb["banco_cpf_cnpj"] == nota_doc)
            & (banco_bb["data_movimento"] >= inicio)
            & (banco_bb["data_movimento"] <= fim)
            & (banco_bb["valor"] < nota["valor"])
        ].copy()
        if len(candidatos) < 2:
            return pd.DataFrame()

        candidatos = candidatos.sort_values(["valor", "descricao"], ascending=[False, True]).head(8)
        alvo = nota["valor"]
        indices = list(candidatos.index)
        for tamanho in range(2, min(5, len(indices)) + 1):
            for combo in combinations(indices, tamanho):
                soma = sum((candidatos.loc[idx, "valor"] for idx in combo), Decimal("0"))
                if soma == alvo:
                    return candidatos.loc[list(combo)]
        return pd.DataFrame()

    def _priorizar_match_por_documento(self, nota: pd.Series, match: pd.DataFrame, exigir_cpf: bool = False) -> pd.DataFrame:
        if match.empty:
            return match

        ordenado = match.copy()
        nota_doc = str(nota.get("cpf_tomador_digits") or "")
        if nota_doc and "banco_cpf_cnpj" in ordenado.columns:
            ordenado["_doc_match"] = ordenado["banco_cpf_cnpj"] == nota_doc
            if ordenado["_doc_match"].any():
                ordenado = ordenado[ordenado["_doc_match"]]
            elif exigir_cpf:
                return pd.DataFrame()
        elif exigir_cpf:
            return pd.DataFrame()
        else:
            ordenado["_doc_match"] = False

        ordenado["_diff_dias"] = ordenado["data_movimento"].apply(
            lambda data_movimento: abs((nota["data_movimento"] - data_movimento).days)
        )
        ordenado = ordenado.sort_values(
            ["_doc_match", "_diff_dias", "data_movimento", "valor"],
            ascending=[False, True, True, True],
        )
        return ordenado.drop(columns=["_doc_match", "_diff_dias"]).head(1)

    def _build_nota_row(self, sessao: SessaoConciliacao, row: dict[str, Any], index: int) -> dict[str, Any] | None:
        data_movimento = self._parse_date(
            row.get("data_pagamento")
            or row.get("data")
            or row.get("data_movimento")
            or row.get("data_emissao")
        )
        valor = self._parse_decimal(row.get("valor"))
        if not data_movimento or valor <= 0:
            return None

        tipo_pagamento = str(row.get("tipo_pagamento") or "").strip()
        data_emissao = self._parse_date(row.get("data_emissao"))
        return {
            "id": row.get("id") or f"{sessao.id}:{index}",
            "sessao_id": str(sessao.id),
            "arquivo_nome": sessao.arquivo_nome,
            "numero_nota": str(row.get("numero_nota") or ""),
            "tomador": str(row.get("nome_tomador") or row.get("tomador") or row.get("historico") or ""),
            "cpf_tomador": str(row.get("cpf_tomador") or row.get("documento_tomador") or ""),
            "cpf_tomador_digits": self._digits(row.get("cpf_tomador") or row.get("documento_tomador") or ""),
            "data_emissao": data_emissao,
            "data_movimento": data_movimento,
            "valor": valor,
            "descricao": str(row.get("descricao") or row.get("historico") or ""),
            "tipo_pagamento": tipo_pagamento,
            "tipo_pagamento_norm": self._normalizar_tipo_pagamento(tipo_pagamento),
            "bandeira_cartao_norm": self._normalizar_bandeira_cartao(tipo_pagamento),
            "status_conciliado": bool(row.get("status_conciliado") or row.get("conciliado")),
        }

    def _build_getnet_row(self, sessao: SessaoConciliacao, row: dict[str, Any], index: int) -> dict[str, Any] | None:
        data_movimento = self._parse_date(row.get("data_venda") or row.get("data_movimento") or row.get("data"))
        valor_bruto = self._parse_decimal(row.get("valor_bruto"))
        valor_liquido = self._parse_decimal(row.get("valor_liquido"))
        valor_tarifa = self._parse_decimal(row.get("valor_tarifa"))
        if not data_movimento or valor_bruto <= 0:
            return None

        cartao = str(row.get("cartao") or "")
        return {
            "id": row.get("id") or f"{sessao.id}:{index}",
            "sessao_id": str(sessao.id),
            "arquivo_nome": sessao.arquivo_nome,
            "adquirente": "getnet",
            "adquirente_norm": "GETNET",
            "data_movimento": data_movimento,
            "cartao": cartao,
            "tipo_cartao_norm": self._normalizar_tipo_cartao(cartao),
            "bandeira_cartao_norm": self._normalizar_bandeira_cartao(cartao),
            "codigo_estabelecimento": str(row.get("codigo_estabelecimento") or ""),
            "quantidade": int(row.get("quantidade") or 0),
            "valor_bruto": valor_bruto,
            "valor_tarifa": valor_tarifa,
            "valor_liquido": valor_liquido,
        }

    def _build_banco_row(
        self,
        sessao: SessaoConciliacao,
        row: dict[str, Any],
        index: int,
        banco: str,
    ) -> dict[str, Any] | None:
        data_movimento = self._parse_date(
            row.get("data")
            or row.get("data_movimento")
            or row.get("data_lancamento_extrato")
        )
        valor = self._parse_decimal(row.get("valor_original_do_banco") or row.get("valor"))
        if not data_movimento or valor <= 0:
            return None

        tipo_movimento = str(row.get("natureza") or row.get("tipo_movimento") or "").upper()
        if tipo_movimento not in {TipoMovimento.CREDITO, TipoMovimento.DEBITO}:
            return None

        descricao = str(row.get("descricao_normalizada") or row.get("descricao") or row.get("historico") or "")
        documento = str(row.get("documento") or "")
        descricao_norm = self._normalize(f"{descricao} {documento}")
        banco_cpf_cnpj, banco_nome_extraido = self._extract_banco_documento_nome(f"{descricao} {documento}")
        banco_eh_pix = "PIX" in descricao_norm

        return {
            "id": row.get("id") or f"{sessao.id}:{index}",
            "sessao_id": str(sessao.id),
            "arquivo_nome": sessao.arquivo_nome,
            "data_movimento": data_movimento,
            "valor": valor,
            "descricao": descricao,
            "descricao_norm": descricao_norm,
            "documento": documento,
            "banco_eh_pix": banco_eh_pix,
            "banco_cpf_cnpj": banco_cpf_cnpj,
            "banco_nome_extraido": banco_nome_extraido,
            "bandeira_cartao_norm": self._normalizar_bandeira_cartao(descricao_norm),
            "tipo_movimento": tipo_movimento,
            "banco": banco,
            "banco_norm": self._normalize(banco),
            "status_conciliado": bool(row.get("status_conciliado") or row.get("conciliado")),
        }

    def _is_nfse_session(self, sessao: SessaoConciliacao, lancamentos: Iterable[dict[str, Any]]) -> bool:
        banco = self._normalize(sessao.banco)
        if banco in {"NFSE", "NFSE PREFEITURA", "NOTA FISCAL", "NOTAS FISCAIS"}:
            return True

        return any(isinstance(row, dict) and "tipo_pagamento" in row for row in lancamentos)

    def _is_getnet_session(self, sessao: SessaoConciliacao, lancamentos: Iterable[dict[str, Any]]) -> bool:
        banco = self._normalize(sessao.banco)
        if banco == "GETNET":
            return True

        return any(isinstance(row, dict) and {"cartao", "valor_bruto", "valor_liquido"}.issubset(row.keys()) for row in lancamentos)

    def _is_antecipacao_nota(self, nota_grupo: pd.Series | None) -> bool:
        if nota_grupo is None:
            return False
        return bool(nota_grupo.get("antecipado_nota"))

    @staticmethod
    def _digits(value: Any) -> str:
        return re.sub(r"\D", "", str(value or ""))

    @classmethod
    def _extract_banco_documento_nome(cls, text: str) -> tuple[str, str]:
        clean = str(text or "")
        # No BB, o PIX costuma vir como 000 + CPF/CNPJ, por exemplo:
        # 00001478210290 KARLA DANIE -> CPF 01478210290.
        doc_match = re.search(r"0{3}(\d{11}|\d{14})(?!\d)", clean)
        if not doc_match:
            doc_match = re.search(r"(?<!\d)(\d{11}|\d{14})(?!\d)", clean)
        if not doc_match:
            return "", ""

        documento = doc_match.group(1)
        nome = clean[doc_match.end():].strip(" -:;,.0123456789")
        nome = re.sub(r"\s+", " ", nome).strip()
        return documento, nome

    @classmethod
    def _normalizar_tipo_pagamento(cls, value: Any) -> str:
        normalized = cls._normalize(value).replace(" ", "_")
        aliases = {
            "PAGAMENTO_A_VISTA": "PAGAMENTO_A_VISTA",
            "AVISTA": "PAGAMENTO_A_VISTA",
            "A_VISTA": "PAGAMENTO_A_VISTA",
            "DEPOSITO": "DEPOSITO",
            "PIX": "PIX",
            "TED": "TED",
            "CARTAO_DE_CREDITO": "CARTAO_CREDITO",
            "CARTAO_CREDITO": "CARTAO_CREDITO",
            "CREDITO": "CARTAO_CREDITO",
            "CARTAO_DE_DEBITO": "CARTAO_DEBITO",
            "CARTAO_DEBITO": "CARTAO_DEBITO",
            "DEBITO": "CARTAO_DEBITO",
        }
        if normalized in aliases:
            return aliases[normalized]
        if "CREDITO" in normalized:
            return "CARTAO_CREDITO"
        if "DEBITO" in normalized or "ELECTR" in normalized or "MAESTRO" in normalized:
            return "CARTAO_DEBITO"
        return normalized

    @classmethod
    def _normalizar_tipo_cartao(cls, value: Any) -> str:
        normalized = cls._normalize(value)
        if "CREDITO" in normalized:
            return "CARTAO_CREDITO"
        if "DEBITO" in normalized or "ELECTR" in normalized or "MAESTRO" in normalized:
            return "CARTAO_DEBITO"
        return normalized.replace(" ", "_")

    @classmethod
    def _normalizar_bandeira_cartao(cls, value: Any) -> str:
        normalized = cls._normalize(value).replace(" ", "")
        if not normalized:
            return ""
        if "HIPERCARD" in normalized:
            return "HIPERCARD"
        if "AMEX" in normalized or "AMERICANEXPRESS" in normalized:
            return "AMEX"
        if "ELO" in normalized:
            return "ELO"
        if "VISA" in normalized or "ELECTR" in normalized or "ELECTRON" in normalized:
            return "VISA"
        if "MASTERCARD" in normalized or "MASTER" in normalized or "MAESTRO" in normalized:
            return "MASTERCARD"
        return ""

    @staticmethod
    def _parse_date(value: Any) -> date | None:
        if isinstance(value, date):
            return value
        if not value:
            return None
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None

    @staticmethod
    def _parse_decimal(value: Any) -> Decimal:
        if isinstance(value, Decimal):
            return value.copy_abs()
        if value is None:
            return Decimal("0")
        text = str(value).strip().replace("R$", "").replace(" ", "")
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        try:
            return Decimal(text).copy_abs()
        except (InvalidOperation, ValueError):
            return Decimal("0")

    @staticmethod
    def _normalize(value: Any) -> str:
        text = str(value or "").strip().upper()
        text = unicodedata.normalize("NFD", text)
        text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
        return " ".join(text.replace("_", " ").split())


# Alias temporario para imports antigos que ainda nao foram migrados.
MotorConciliacaoPandas = MotorConsiPixPandas
