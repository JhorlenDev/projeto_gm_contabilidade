from __future__ import annotations

import json

from django.db.models import Q
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from permissions.authentication import KeycloakJWTAuthentication
from permissions.permissions import HasUserGMRole

from services.conciliador import (
    apply_rules_to_importacao,
    detect_tipo_arquivo,
    inspect_importacao_file,
    process_importacao,
)
from services.pdf_parser import process_extrato_pdf
from services.parsers.comprovante import parse_comprovante_pdf

from .models import Cliente, Escritorio, ExtratoHistorico, ImportacaoExtrato, PerfilConciliacao, RegraConciliador, StatusImportacao, TransacaoImportada
from .serializers import (
    ClienteSerializer,
    EscritorioSerializer,
    ImportacaoExtratoSerializer,
    PerfilConciliacaoSerializer,
    RegraConciliadorSerializer,
    TransacaoImportadaSerializer,
)


def _load_json_payload(value):
    if isinstance(value, dict):
        return value

    if isinstance(value, str) and value.strip():
        try:
            payload = json.loads(value)
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}

    return {}


class ClienteViewSet(viewsets.ModelViewSet):
    authentication_classes = [KeycloakJWTAuthentication]
    permission_classes = [HasUserGMRole]
    serializer_class = ClienteSerializer
    queryset = Cliente.objects.all()
    lookup_field = "id"
    lookup_value_regex = r"[0-9a-fA-F-]{36}"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["codigo", "nome", "cpf_cnpj", "ie", "telefone", "situacao"]
    ordering_fields = ["codigo", "nome", "cpf_cnpj", "data_inicio", "situacao", "criado_em"]
    ordering = ["nome"]


class EscritorioViewSet(viewsets.ModelViewSet):
    authentication_classes = [KeycloakJWTAuthentication]
    permission_classes = [HasUserGMRole]
    serializer_class = EscritorioSerializer
    queryset = Escritorio.objects.all()
    lookup_field = "id"
    lookup_value_regex = r"[0-9a-fA-F-]{36}"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["nome", "cnpj"]
    ordering_fields = ["nome", "cnpj", "criado_em"]
    ordering = ["nome"]


class RegraConciliadorViewSet(viewsets.ModelViewSet):
    authentication_classes = [KeycloakJWTAuthentication]
    permission_classes = [HasUserGMRole]
    serializer_class = RegraConciliadorSerializer
    queryset = RegraConciliador.objects.select_related("escritorio", "empresa")
    lookup_field = "id"
    lookup_value_regex = r"[0-9a-fA-F-]{36}"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["nome", "texto_referencia", "categoria", "subcategoria", "codigo_historico"]
    ordering_fields = ["prioridade", "nome", "criado_em", "atualizado_em"]
    ordering = ["prioridade", "nome"]

    def get_queryset(self):
        queryset = super().get_queryset()
        escritorio_id = self.request.query_params.get("escritorio")
        empresa_id = self.request.query_params.get("empresa")

        if escritorio_id:
            queryset = queryset.filter(escritorio_id=escritorio_id)

        if empresa_id:
            queryset = queryset.filter(Q(empresa_id=empresa_id) | Q(empresa__isnull=True))

        ativo = self.request.query_params.get("ativo")
        if ativo in {"true", "1", "yes"}:
            queryset = queryset.filter(ativo=True)
        elif ativo in {"false", "0", "no"}:
            queryset = queryset.filter(ativo=False)

        return queryset


class ImportacaoExtratoViewSet(viewsets.ModelViewSet):
    authentication_classes = [KeycloakJWTAuthentication]
    permission_classes = [HasUserGMRole]
    serializer_class = ImportacaoExtratoSerializer
    queryset = ImportacaoExtrato.objects.select_related("escritorio", "empresa").prefetch_related("transacoes", "transacoes__regra_aplicada")
    lookup_field = "id"
    lookup_value_regex = r"[0-9a-fA-F-]{36}"
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["referencia", "escritorio__nome", "empresa__nome", "tipo_arquivo", "status"]
    ordering_fields = ["criado_em", "atualizado_em", "referencia", "tipo_arquivo", "status"]
    ordering = ["-criado_em"]

    def get_queryset(self):
        queryset = super().get_queryset()
        escritorio_id = self.request.query_params.get("escritorio")
        empresa_id = self.request.query_params.get("empresa")
        referencia = self.request.query_params.get("referencia")
        status_param = self.request.query_params.get("status")

        if escritorio_id:
            queryset = queryset.filter(escritorio_id=escritorio_id)
        if empresa_id:
            queryset = queryset.filter(empresa_id=empresa_id)
        if referencia:
            queryset = queryset.filter(referencia=referencia)
        if status_param:
            queryset = queryset.filter(status=status_param)

        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data["arquivo"]
        tipo_arquivo = detect_tipo_arquivo(getattr(upload, "name", ""))
        configuracao = _load_json_payload(request.data.get("configuracao") or request.data.get("metadados"))

        importacao = serializer.save(tipo_arquivo=tipo_arquivo, status=StatusImportacao.ENVIADA, configuracao=configuracao)
        try:
            importacao.metadados = inspect_importacao_file(importacao)
            importacao.mensagem_erro = ""
        except (ImportError, ValueError) as exc:
            importacao.metadados = {}
            importacao.mensagem_erro = str(exc)
        importacao.save(update_fields=["metadados", "mensagem_erro", "atualizado_em"])

        return Response(self.get_serializer(importacao).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def processar(self, request, id=None):
        importacao = self.get_object()
        configuracao = _load_json_payload(request.data.get("configuracao") or request.data.get("config") or importacao.configuracao)

        importacao.configuracao = configuracao
        importacao.status = StatusImportacao.PROCESSANDO
        importacao.mensagem_erro = ""
        importacao.save(update_fields=["configuracao", "status", "mensagem_erro", "atualizado_em"])

        try:
            summary = process_importacao(importacao, configuracao=configuracao)
        except (ImportError, ValueError) as exc:
            importacao.status = StatusImportacao.ERRO
            importacao.mensagem_erro = str(exc)
            importacao.save(update_fields=["status", "mensagem_erro", "atualizado_em"])
            payload = self.get_serializer(importacao).data
            payload["detail"] = str(exc)
            return Response(payload, status=status.HTTP_400_BAD_REQUEST)

        importacao.status = StatusImportacao.PROCESSADA
        importacao.save(update_fields=["status", "atualizado_em"])

        payload = self.get_serializer(importacao).data
        payload["summary"] = summary
        return Response(payload)

    @action(detail=True, methods=["post"])
    def aplicar_regras(self, request, id=None):
        importacao = self.get_object()
        summary = apply_rules_to_importacao(importacao)
        payload = self.get_serializer(importacao).data
        payload["summary"] = summary
        return Response(payload)

    @action(detail=True, methods=["get"])
    def transacoes(self, request, id=None):
        importacao = self.get_object()
        queryset = importacao.transacoes.select_related("regra_aplicada").all()
        serializer = TransacaoImportadaSerializer(queryset, many=True, context=self.get_serializer_context())
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def resultado(self, request, id=None):
        importacao = self.get_object()
        queryset = importacao.transacoes.select_related("regra_aplicada").all()
        serializer = TransacaoImportadaSerializer(queryset, many=True, context=self.get_serializer_context())
        return Response({
            "importacao": self.get_serializer(importacao).data,
            "resultado": serializer.data,
        })


class TransacaoImportadaViewSet(viewsets.ModelViewSet):
    authentication_classes = [KeycloakJWTAuthentication]
    permission_classes = [HasUserGMRole]
    serializer_class = TransacaoImportadaSerializer
    queryset = TransacaoImportada.objects.select_related("importacao", "regra_aplicada", "importacao__escritorio", "importacao__empresa")
    lookup_field = "id"
    lookup_value_regex = r"[0-9a-fA-F-]{36}"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["descricao_original", "descricao_normalizada", "categoria", "subcategoria", "codigo_historico"]
    ordering_fields = ["data_movimento", "valor", "criado_em", "atualizado_em"]
    ordering = ["data_movimento", "id"]

    def get_queryset(self):
        queryset = super().get_queryset()
        importacao_id = self.request.query_params.get("importacao")
        if importacao_id:
            queryset = queryset.filter(importacao_id=importacao_id)

        tipo_movimento = self.request.query_params.get("tipo_movimento")
        if tipo_movimento:
            queryset = queryset.filter(tipo_movimento=tipo_movimento)

        pendente = self.request.query_params.get("pendente")
        if pendente in {"true", "1", "yes"}:
            queryset = queryset.filter(regra_aplicada__isnull=True)
        elif pendente in {"false", "0", "no"}:
            queryset = queryset.filter(regra_aplicada__isnull=False)

        return queryset

    def perform_update(self, serializer):
        serializer.save(revisado_manual=True)


class PerfilConciliacaoViewSet(viewsets.ModelViewSet):
    authentication_classes = [KeycloakJWTAuthentication]
    permission_classes = [HasUserGMRole]
    serializer_class = PerfilConciliacaoSerializer
    queryset = PerfilConciliacao.objects.select_related("escritorio", "empresa")
    lookup_field = "id"
    lookup_value_regex = r"[0-9a-fA-F-]{36}"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["nome", "descricao", "conta_bancaria", "codigo_historico", "codigo_empresa", "cnpj"]
    ordering_fields = ["nome", "criado_em", "atualizado_em"]
    ordering = ["nome"]

    def get_queryset(self):
        queryset = super().get_queryset()
        escritorio_id = self.request.query_params.get("escritorio")
        empresa_id = self.request.query_params.get("empresa")

        if escritorio_id:
            queryset = queryset.filter(escritorio_id=escritorio_id)
        if empresa_id:
            queryset = queryset.filter(empresa_id=empresa_id)

        ativo = self.request.query_params.get("ativo")
        if ativo in {"true", "1", "yes"}:
            queryset = queryset.filter(ativo=True)
        elif ativo in {"false", "0", "no"}:
            queryset = queryset.filter(ativo=False)

        return queryset


class ExtratoPreviewView(APIView):
    """
    POST /api/extrato-preview/
    Recebe um arquivo PDF de extrato bancário e retorna os lançamentos parseados
    sem salvar nada no banco de dados.

    Form fields:
      - arquivo: arquivo PDF
      - banco: "bradesco" | "auto" (default: "auto")
    """
    authentication_classes = [KeycloakJWTAuthentication]
    permission_classes = [HasUserGMRole]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        arquivo = request.FILES.get("arquivo")
        if not arquivo:
            return Response({"detail": "Nenhum arquivo enviado."}, status=status.HTTP_400_BAD_REQUEST)

        ext = arquivo.name.rsplit(".", 1)[-1].lower() if "." in arquivo.name else ""
        if ext != "pdf":
            return Response({"detail": "Apenas arquivos PDF são suportados."}, status=status.HTTP_400_BAD_REQUEST)

        banco = request.data.get("banco", "auto").lower().strip()

        resultado = process_extrato_pdf(arquivo, banco=banco)

        if not resultado.success:
            return Response(
                {"detail": "; ".join(resultado.erros) or "Falha ao processar o PDF."},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        lancamentos = [
            {
                "linha": l.linha_origem,
                "data": l.data.isoformat() if l.data else None,
                "historico": l.descricao_original,
                "documento": l.documento,
                "valor": str(l.valor),
                "natureza": l.natureza_inferida or "INDEFINIDA",
                "saldo": str(l.saldo) if l.saldo is not None else None,
            }
            for l in resultado.lancamentos
        ]

        header = resultado.header
        return Response({
            "banco": banco if banco != "auto" else "auto-detectado",
            "empresa_nome": header.empresa_nome,
            "empresa_cnpj": header.empresa_cnpj,
            "agencia": header.agencia,
            "conta": header.conta,
            "periodo_inicio": header.periodo_inicio.isoformat() if header.periodo_inicio else None,
            "periodo_fim": header.periodo_fim.isoformat() if header.periodo_fim else None,
            "saldo_final": str(header.saldo),
            "total": resultado.total_lancamentos,
            "avisos": resultado.avisos,
            "lancamentos": lancamentos,
        })


class ComprovantePreviw(APIView):
    """
    POST /api/comprovante-preview/
    Recebe um ou mais arquivos PDF de comprovante e retorna os dados parseados.

    Form fields:
      - arquivos: um ou mais PDFs de comprovante (campo multi-arquivo)
    """
    authentication_classes = [KeycloakJWTAuthentication]
    permission_classes = [HasUserGMRole]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        arquivos = request.FILES.getlist("arquivos")
        if not arquivos:
            # Suporte a campo único chamado "arquivo" também
            arq = request.FILES.get("arquivo")
            if arq:
                arquivos = [arq]
        if not arquivos:
            return Response({"detail": "Nenhum arquivo enviado."}, status=status.HTTP_400_BAD_REQUEST)

        comprovantes = []
        erros = []

        for arquivo in arquivos:
            ext = arquivo.name.rsplit(".", 1)[-1].lower() if "." in arquivo.name else ""
            if ext != "pdf":
                erros.append(f"{arquivo.name}: apenas PDFs são suportados.")
                continue

            resultados = parse_comprovante_pdf(arquivo)
            for r in resultados:
                if not r.success:
                    erros.extend(r.erros)
                    continue
                comprovantes.append({
                    "tipo": r.tipo,
                    "documento": r.documento,
                    "data_pagamento": r.data_pagamento.isoformat() if r.data_pagamento else None,
                    "beneficiario": r.beneficiario,
                    "valor_total": str(r.valor_total),
                    "itens": [
                        {"descricao": it.descricao, "valor": str(it.valor)}
                        for it in r.itens
                        if it.valor != 0
                    ],
                })

        return Response({
            "total": len(comprovantes),
            "comprovantes": comprovantes,
            "avisos": erros,
        })


class ExtratoHistoricoView(APIView):
    """
    GET  /api/extrato-historico/?empresa=<uuid>  — lista históricos da empresa
    POST /api/extrato-historico/                  — salva novo histórico
    DELETE /api/extrato-historico/<id>/           — remove histórico
    """
    authentication_classes = [KeycloakJWTAuthentication]
    permission_classes = [HasUserGMRole]

    def get(self, request):
        empresa_id = request.query_params.get("empresa")
        if not empresa_id:
            return Response({"detail": "Parâmetro 'empresa' obrigatório."}, status=status.HTTP_400_BAD_REQUEST)

        qs = ExtratoHistorico.objects.filter(empresa_id=empresa_id).order_by("-criado_em")
        data = [
            {
                "id": str(h.id),
                "banco": h.banco,
                "periodo_inicio": h.periodo_inicio.isoformat() if h.periodo_inicio else None,
                "periodo_fim": h.periodo_fim.isoformat() if h.periodo_fim else None,
                "total_lancamentos": h.total_lancamentos,
                "criado_em": h.criado_em.isoformat(),
                # Não retorna 'dados' completo na listagem por performance
            }
            for h in qs
        ]
        return Response({"historicos": data, "total": len(data)})

    def post(self, request):
        empresa_id = request.data.get("empresa")
        escritorio_id = request.data.get("escritorio")
        if not empresa_id:
            return Response({"detail": "Campo 'empresa' obrigatório."}, status=status.HTTP_400_BAD_REQUEST)

        # Carrega escritorio automaticamente se não fornecido
        if not escritorio_id:
            esc = Escritorio.objects.first()
            if not esc:
                return Response({"detail": "Nenhum escritório cadastrado."}, status=status.HTTP_400_BAD_REQUEST)
            escritorio_id = str(esc.id)

        try:
            empresa = Cliente.objects.get(pk=empresa_id)
            escritorio = Escritorio.objects.get(pk=escritorio_id)
        except (Cliente.DoesNotExist, Escritorio.DoesNotExist) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)

        dados = {
            "lancamentos": request.data.get("lancamentos", []),
            "regras": request.data.get("regras", {}),
            "componentes": request.data.get("componentes", {}),
            "comprovantes": request.data.get("comprovantes", {}),
            "extratoMeta": request.data.get("extratoMeta", {}),
        }

        from datetime import date
        def _parse_date(v):
            try:
                return date.fromisoformat(v) if v else None
            except (ValueError, TypeError):
                return None

        h = ExtratoHistorico.objects.create(
            empresa=empresa,
            escritorio=escritorio,
            banco=request.data.get("banco", ""),
            periodo_inicio=_parse_date(request.data.get("periodo_inicio")),
            periodo_fim=_parse_date(request.data.get("periodo_fim")),
            total_lancamentos=len(dados["lancamentos"]),
            dados=dados,
        )
        return Response({
            "id": str(h.id),
            "criado_em": h.criado_em.isoformat(),
            "total_lancamentos": h.total_lancamentos,
        }, status=status.HTTP_201_CREATED)

    def delete(self, request, pk=None):
        if not pk:
            return Response({"detail": "ID obrigatório."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            h = ExtratoHistorico.objects.get(pk=pk)
        except ExtratoHistorico.DoesNotExist:
            return Response({"detail": "Não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        h.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)