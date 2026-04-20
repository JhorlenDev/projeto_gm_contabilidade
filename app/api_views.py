from __future__ import annotations

import json

from django.db.models import Q
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from permissions.authentication import KeycloakJWTAuthentication
from permissions.permissions import HasUserGMRole

from services.conciliador import (
    apply_rules_to_importacao,
    detect_tipo_arquivo,
    inspect_importacao_file,
    process_importacao,
)
from services.pdf_parser import process_extrato_pdf

from .models import Cliente, Escritorio, ImportacaoExtrato, RegraConciliador, StatusImportacao, TransacaoImportada
from .serializers import (
    ClienteSerializer,
    EscritorioSerializer,
    ImportacaoExtratoSerializer,
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
