from __future__ import annotations

import json
from decimal import Decimal

from django.db.models import Q
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from permissions.authentication import KeycloakJWTAuthentication
from permissions.permissions import HasUserGMRole

from services.conciliador import (
    apply_rules_to_importacao,
    detect_tipo_arquivo,
    describe_transaction_metadata,
    inspect_importacao_file,
    normalizar_descricao_transacao,
    process_importacao,
)
from services.keycloak import KeycloakConfigurationError, KeycloakTokenError, exchange_code_for_token
from services.pdf_parser import process_extrato_pdf
from services.parsers.comprovante import parse_comprovante_pdf

from .models import (
    Banco,
    CertificadoDigitalCliente,
    Cliente,
    ContaCliente,
    Escritorio,
    ExtratoHistorico,
    HistoricoContabil,
    ImportacaoExtrato,
    OrigemAuditoriaTarifa,
    PerfilConciliacao,
    PlanoContas,
    RegraConciliador,
    StatusImportacao,
    TarifaVinculoAuditoria,
    TipoComponenteLancamento,
    TransacaoImportada,
)
from .serializers import (
    BancoSerializer,
    ClienteSerializer,
    CertificadoDigitalClienteSerializer,
    ContaClienteSerializer,
    EscritorioSerializer,
    HistoricoContabilSerializer,
    ImportacaoExtratoSerializer,
    PerfilConciliacaoSerializer,
    PlanoContasSerializer,
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


class KeycloakTokenExchangeView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [JSONParser, FormParser]

    def post(self, request):
        code = str(request.data.get("code") or "").strip()
        verifier = str(request.data.get("verifier") or request.data.get("code_verifier") or "").strip()
        redirect_uri = str(request.data.get("redirect_uri") or "").strip()

        if not code or not verifier or not redirect_uri:
            return Response(
                {
                    "error": "invalid_request",
                    "error_description": "code, verifier e redirect_uri são obrigatórios.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            response_status, payload = exchange_code_for_token(code, verifier, redirect_uri)
        except KeycloakConfigurationError as exc:
            return Response(
                {
                    "error": "keycloak_configuration_error",
                    "error_description": str(exc),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except KeycloakTokenError as exc:
            return Response(
                {
                    "error": "keycloak_token_error",
                    "error_description": str(exc),
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(payload, status=response_status)


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


class ContaClienteViewSet(viewsets.ModelViewSet):
    authentication_classes = [KeycloakJWTAuthentication]
    permission_classes = [HasUserGMRole]
    serializer_class = ContaClienteSerializer
    queryset = ContaCliente.objects.select_related("cliente")
    lookup_field = "id"
    lookup_value_regex = r"[0-9a-fA-F-]{36}"
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["apelido", "banco", "agencia", "numero", "codigo_contabil", "descricao_contabil"]
    ordering_fields = ["tipo", "apelido", "ativo", "criado_em", "atualizado_em"]
    ordering = ["-ativo", "tipo", "apelido"]

    def get_queryset(self):
        queryset = super().get_queryset()
        cliente_id = self.request.query_params.get("cliente")
        ativo = self.request.query_params.get("ativo")
        tipo = self.request.query_params.get("tipo")

        if cliente_id:
            queryset = queryset.filter(cliente_id=cliente_id)

        if ativo in {"true", "1", "yes"}:
            queryset = queryset.filter(ativo=True)
        elif ativo in {"false", "0", "no"}:
            queryset = queryset.filter(ativo=False)

        if tipo:
            queryset = queryset.filter(tipo=tipo)

        return queryset

    def destroy(self, request, *args, **kwargs):
        conta = self.get_object()
        conta.ativo = False
        conta.save(update_fields=["ativo", "atualizado_em"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class CertificadoDigitalClienteViewSet(viewsets.ModelViewSet):
    authentication_classes = [KeycloakJWTAuthentication]
    permission_classes = [HasUserGMRole]
    serializer_class = CertificadoDigitalClienteSerializer
    queryset = CertificadoDigitalCliente.objects.select_related("cliente")
    lookup_field = "id"
    lookup_value_regex = r"[0-9a-fA-F-]{36}"
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["cliente__nome", "arquivo_original", "tipo_arquivo"]
    ordering_fields = ["cliente__nome", "criado_em", "atualizado_em"]
    ordering = ["cliente__nome"]

    def get_queryset(self):
        queryset = super().get_queryset()
        cliente_id = self.request.query_params.get("cliente")
        ativo = self.request.query_params.get("ativo")

        if cliente_id:
            queryset = queryset.filter(cliente_id=cliente_id)

        if ativo in {"true", "1", "yes"}:
            queryset = queryset.filter(ativo=True)
        elif ativo in {"false", "0", "no"}:
            queryset = queryset.filter(ativo=False)

        return queryset

    def destroy(self, request, *args, **kwargs):
        certificado = self.get_object()
        arquivo = certificado.arquivo
        arquivo_name = arquivo.name if arquivo else ""
        if arquivo_name:
            arquivo.storage.delete(arquivo_name)
        certificado.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


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


class BancoViewSet(viewsets.ModelViewSet):
    authentication_classes = [KeycloakJWTAuthentication]
    permission_classes = [HasUserGMRole]
    serializer_class = BancoSerializer
    queryset = Banco.objects.all()
    lookup_field = "id"
    lookup_value_regex = r"[0-9a-fA-F-]{36}"
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["codigo", "nome", "slug", "sigla"]
    ordering_fields = ["codigo", "nome", "ativo", "criado_em", "atualizado_em"]
    ordering = ["nome"]

    def get_queryset(self):
        queryset = super().get_queryset()
        ativo = self.request.query_params.get("ativo")

        if ativo in {"true", "1", "yes"}:
            queryset = queryset.filter(ativo=True)
        elif ativo in {"false", "0", "no"}:
            queryset = queryset.filter(ativo=False)

        return queryset

    def destroy(self, request, *args, **kwargs):
        banco = self.get_object()
        banco.ativo = False
        banco.save(update_fields=["ativo", "atualizado_em"])
        return Response(status=status.HTTP_204_NO_CONTENT)


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

    def _aplicar_regras_apos_salvar(self, regra):
        importacao_id = self.request.query_params.get("importacao")
        empresa = self.request.query_params.get("empresa")

        if importacao_id:
            importacoes = ImportacaoExtrato.objects.filter(id=importacao_id, escritorio=regra.escritorio)
        elif empresa:
            importacoes = ImportacaoExtrato.objects.filter(empresa_id=empresa, escritorio=regra.escritorio, status=StatusImportacao.PROCESSADA)
        else:
            importacoes = ImportacaoExtrato.objects.filter(escritorio=regra.escritorio, status=StatusImportacao.PROCESSADA)
            if regra.empresa:
                importacoes = importacoes.filter(empresa=regra.empresa)

        for imp in importacoes:
            apply_rules_to_importacao(imp)

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        if response.status_code == 201:
            regra = RegraConciliador.objects.get(pk=response.data["id"])
            self._aplicar_regras_apos_salvar(regra)
        return response

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        if response.status_code == 200:
            regra = self.get_object()
            self._aplicar_regras_apos_salvar(regra)
        return response


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
        queryset = importacao.transacoes.select_related("regra_aplicada", "lancamento_relacionado").prefetch_related("componentes").all()
        serializer = TransacaoImportadaSerializer(queryset, many=True, context=self.get_serializer_context())
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def resultado(self, request, id=None):
        importacao = self.get_object()
        queryset = importacao.transacoes.select_related("regra_aplicada", "lancamento_relacionado").prefetch_related("componentes").all()
        serializer = TransacaoImportadaSerializer(queryset, many=True, context=self.get_serializer_context())
        return Response({
            "importacao": self.get_serializer(importacao).data,
            "resultado": serializer.data,
        })


class TransacaoImportadaViewSet(viewsets.ModelViewSet):
    authentication_classes = [KeycloakJWTAuthentication]
    permission_classes = [HasUserGMRole]
    serializer_class = TransacaoImportadaSerializer
    queryset = TransacaoImportada.objects.select_related(
        "importacao",
        "regra_aplicada",
        "lancamento_relacionado",
        "importacao__escritorio",
        "importacao__empresa",
    ).prefetch_related("componentes")
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

        chave_descricao = self.request.query_params.get("chave_descricao") or self.request.query_params.get("descricao_normalizada")
        if chave_descricao:
            chave_descricao = normalizar_descricao_transacao(chave_descricao)
            matching_ids = [
                transacao.id
                for transacao in queryset.only("id", "descricao_original")
                if normalizar_descricao_transacao(transacao.descricao_original) == chave_descricao
            ]
            queryset = queryset.filter(id__in=matching_ids)

        similar_a = self.request.query_params.get("similar_a")
        if similar_a:
            try:
                referencia = TransacaoImportada.objects.only("importacao_id", "descricao_original").get(id=similar_a)
            except TransacaoImportada.DoesNotExist:
                return queryset.none()
            chave_referencia = normalizar_descricao_transacao(referencia.descricao_original)
            matching_ids = [
                transacao.id
                for transacao in queryset.filter(importacao_id=referencia.importacao_id).only("id", "descricao_original")
                if normalizar_descricao_transacao(transacao.descricao_original) == chave_referencia
            ]
            queryset = queryset.filter(
                id__in=matching_ids,
            )

        tipo_movimento = self.request.query_params.get("tipo_movimento")
        if tipo_movimento:
            queryset = queryset.filter(tipo_movimento=tipo_movimento)

        pendente = self.request.query_params.get("pendente")
        if pendente in {"true", "1", "yes"}:
            queryset = queryset.filter(regra_aplicada__isnull=True)
        elif pendente in {"false", "0", "no"}:
            queryset = queryset.filter(regra_aplicada__isnull=False)

        return queryset

    @action(detail=True, methods=["get"])
    def similares(self, request, pk=None):
        transacao = self.get_object()
        chave_descricao = normalizar_descricao_transacao(transacao.descricao_original)
        queryset = self.get_queryset()
        if request.query_params.get("mesma_importacao", "true").lower() not in {"false", "0", "no"}:
            queryset = queryset.filter(importacao_id=transacao.importacao_id)
        matching_ids = [
            item.id
            for item in queryset.only("id", "descricao_original")
            if normalizar_descricao_transacao(item.descricao_original) == chave_descricao
        ]
        queryset = queryset.filter(id__in=matching_ids)
        serializer = self.get_serializer(queryset.order_by("data_movimento", "id"), many=True)
        return Response(
            {
                "chave_descricao": chave_descricao,
                "total": len(serializer.data),
                "resultados": serializer.data,
            }
        )

    def perform_update(self, serializer):
        instance = self.get_object()
        status_anterior = instance.status_vinculo_tarifa
        tarifa_anterior_id = instance.lancamento_relacionado_id

        updated = serializer.save(revisado_manual=True)

        if (
            status_anterior != updated.status_vinculo_tarifa
            or tarifa_anterior_id != updated.lancamento_relacionado_id
        ):
            user = getattr(self.request, "user", None)
            usuario = (
                getattr(user, "username", "")
                or getattr(user, "email", "")
                or getattr(user, "sub", "")
                or "SISTEMA"
            )
            TarifaVinculoAuditoria.objects.create(
                lancamento_principal=updated,
                lancamento_tarifa=updated.lancamento_relacionado,
                usuario=usuario,
                origem=OrigemAuditoriaTarifa.MANUAL,
                status_anterior=status_anterior,
                status_novo=updated.status_vinculo_tarifa,
            )

    @action(detail=False, methods=["post"])
    def conciliar_tarifas(self, request):
        importacao_id = request.data.get("importacao_id")
        if not importacao_id:
            return Response({"detail": "importacao_id é obrigatório."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            importacao = ImportacaoExtrato.objects.get(id=importacao_id)
        except ImportacaoExtrato.DoesNotExist:
            return Response({"detail": "Importação não encontrada."}, status=status.HTTP_404_NOT_FOUND)

        from services.conciliador import conciliar_tarifas_importacao

        resultado = conciliar_tarifas_importacao(importacao)
        return Response(resultado)

    @action(detail=True, methods=["post"])
    def processar_comprovante(self, request, pk=None):
        transacao = self.get_object()

        comprovante_data = {
            "tipo": request.data.get("tipo", "").lower(),
            "valor_principal": request.data.get("valor_principal", 0),
            "tarifa_valor": request.data.get("tarifa_valor", 0),
            "juros_valor": request.data.get("juros_valor", 0),
            "multa_valor": request.data.get("multa_valor", 0),
            "desconto_valor": request.data.get("desconto_valor", 0),
            "documento": request.data.get("documento", ""),
            "data_pagamento": request.data.get("data_pagamento"),
            "beneficiario": request.data.get("beneficiario", ""),
        }

        if not comprovante_data["tipo"]:
            return Response({"detail": "tipo é obrigatório (pix, ted, boleto, convenio)."}, status=status.HTTP_400_BAD_REQUEST)

        from services.conciliador import processar_comprovante

        user = getattr(request, "user", None)
        usuario = (
            getattr(user, "username", "")
            or getattr(user, "email", "")
            or getattr(user, "sub", "")
            or "SISTEMA"
        )

        resultado = processar_comprovante(transacao, comprovante_data, usuario=usuario)
        return Response(resultado)

    @action(detail=False, methods=["get"])
    def exportar(self, request):
        importacao_id = request.query_params.get("importacao_id")
        if not importacao_id:
            return Response({"detail": "importacao_id é obrigatório."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            importacao = ImportacaoExtrato.objects.get(id=importacao_id)
        except ImportacaoExtrato.DoesNotExist:
            return Response({"detail": "Importação não encontrada."}, status=status.HTTP_404_NOT_FOUND)

        formato = request.query_params.get("formato", "json").lower()
        transacoes = TransacaoImportada.objects.filter(importacao=importacao).prefetch_related("componentes").order_by("data_movimento", "id")

        if formato == "csv":
            import csv
            import io

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                "Data", "Tipo", "Descrição", "Chave Descrição", "Valor Principal", "Juros", "Multa", "Desconto", "Tarifa",
                "Conta Débito", "Conta Crédito", "Histórico", "Categoria", "Subcategoria", "Status Tarifa"
            ])

            for t in transacoes:
                principal = Decimal("0")
                juros = Decimal("0")
                multa = Decimal("0")
                desconto = Decimal("0")
                tarifa = Decimal("0")

                for comp in t.componentes.all():
                    if comp.tipo_componente == TipoComponenteLancamento.PRINCIPAL:
                        principal = comp.valor
                    elif comp.tipo_componente == TipoComponenteLancamento.JUROS:
                        juros = comp.valor
                    elif comp.tipo_componente == TipoComponenteLancamento.MULTA:
                        multa = comp.valor
                    elif comp.tipo_componente == TipoComponenteLancamento.DESCONTO:
                        desconto = comp.valor

                if t.lancamento_relacionado:
                    tarifa = t.lancamento_relacionado.valor

                chave_descricao = normalizar_descricao_transacao(t.descricao_original)

                writer.writerow([
                    t.data_movimento.isoformat() if t.data_movimento else "",
                    t.get_tipo_movimento_display(),
                    t.descricao_original[:100],
                    chave_descricao,
                    str(principal),
                    str(juros),
                    str(multa),
                    str(desconto),
                    str(tarifa),
                    t.conta_debito,
                    t.conta_credito,
                    t.codigo_historico,
                    t.categoria,
                    t.subcategoria,
                    t.get_status_vinculo_tarifa_display(),
                ])

                if t.lancamento_relacionado:
                    writer.writerow([
                        t.lancamento_relacionado.data_movimento.isoformat() if t.lancamento_relacionado.data_movimento else "",
                        "TARIFA",
                        t.lancamento_relacionado.descricao_original[:100],
                        normalizar_descricao_transacao(t.lancamento_relacionado.descricao_original),
                        "",
                        "",
                        "",
                        "",
                        str(t.lancamento_relacionado.valor),
                        "",
                        "",
                        "",
                        "",
                        "",
                        t.lancamento_relacionado.get_status_vinculo_tarifa_display() if t.lancamento_relacionado else "",
                    ])

            output.seek(0)
            from django.http import StreamingHttpResponse

            response = StreamingHttpResponse(output, content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="conciliacao_{importacao.referencia}.csv"'
            return response

        from decimal import Decimal
        from app.models import TipoComponenteLancamento

        dados = []
        for t in transacoes:
            item = {
                "id": str(t.id),
                "data": t.data_movimento.isoformat() if t.data_movimento else None,
                "descricao": t.descricao_original,
                "chave_descricao": normalizar_descricao_transacao(t.descricao_original),
                "tipo_movimento": t.tipo_movimento,
                "valor_total": str(t.valor),
                "componentes": [],
                "tarifa": None,
                "conta_debito": t.conta_debito,
                "conta_credito": t.conta_credito,
                "codigo_historico": t.codigo_historico,
                "categoria": t.categoria,
                "subcategoria": t.subcategoria,
                "status_tarifa": t.status_vinculo_tarifa,
            }

            for comp in t.componentes.all():
                item["componentes"].append({
                    "tipo": comp.tipo_componente,
                    "valor": str(comp.valor),
                    "descricao": comp.descricao,
                })

            if t.lancamento_relacionado:
                item["tarifa"] = {
                    "id": str(t.lancamento_relacionado.id),
                    "valor": str(t.lancamento_relacionado.valor),
                    "descricao": t.lancamento_relacionado.descricao_original,
                    "data": t.lancamento_relacionado.data_movimento.isoformat() if t.lancamento_relacionado.data_movimento else None,
                }

            dados.append(item)

        return Response({
            "importacao": str(importacao.id),
            "referencia": importacao.referencia,
            "total_lancamentos": len(dados),
            "lancamentos": dados,
        })


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

        lancamentos = []
        for l in resultado.lancamentos:
            descricao_normalizada = normalizar_descricao_transacao(l.descricao_original)
            metadata = describe_transaction_metadata(
                l.descricao_original,
                descricao_normalizada=descricao_normalizada,
                data_movimento=l.data,
            )
            lancamentos.append(
                {
                    "linha": l.linha_origem,
                    "data": l.data.isoformat() if l.data else None,
                    "data_lancamento_extrato": l.data.isoformat() if l.data else None,
                    "data_ocorrencia": metadata["data_ocorrencia"].isoformat() if metadata["data_ocorrencia"] else None,
                    "historico": l.descricao_original,
                    "descricao": l.descricao_original,
                    "chave_descricao": descricao_normalizada,
                    "descricao_normalizada": descricao_normalizada,
                    "documento": l.documento,
                    "valor": str(l.valor),
                    "valor_original_do_banco": str(l.valor),
                    "natureza": l.natureza_inferida or "INDEFINIDA",
                    "tipo_lancamento": metadata["tipo_lancamento"],
                    "status_vinculo_tarifa": "NAO_APLICA",
                    "confianca_vinculo": "BAIXA",
                    "saldo": str(l.saldo) if l.saldo is not None else None,
                }
            )

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
                    "arquivo_nome": arquivo.name,
                    "pagina": r.pagina,
                    "documento": r.documento,
                    "data_pagamento": r.data_pagamento.isoformat() if r.data_pagamento else None,
                    "beneficiario": r.beneficiario,
                    "valor_documento": str(r.valor_documento),
                    "valor_total": str(r.valor_total),
                    "tarifa_valor": str(r.tarifa_valor),
                    "juros_valor": str(r.juros_valor),
                    "multa_valor": str(r.multa_valor),
                    "desconto_valor": str(r.desconto_valor),
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


class PlanoContasView(APIView):
    authentication_classes = [KeycloakJWTAuthentication]
    permission_classes = [HasUserGMRole]

    def _get_escritorio(self, request):
        esc_id = request.query_params.get("escritorio")
        if esc_id:
            try:
                return Escritorio.objects.get(pk=esc_id)
            except Escritorio.DoesNotExist:
                pass
        return Escritorio.objects.first()

    def get(self, request):
        escritorio = self._get_escritorio(request)
        if not escritorio:
            return Response({"detail": "Escritório não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        qs = PlanoContas.objects.filter(escritorio=escritorio).order_by("codigo")
        if not request.query_params.get("todos"):
            qs = qs.filter(ativo=True)
        serializer = PlanoContasSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        escritorio = self._get_escritorio(request)
        if not escritorio:
            return Response({"detail": "Escritório não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        serializer = PlanoContasSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(escritorio=escritorio)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def patch(self, request, pk=None):
        if not pk:
            return Response({"detail": "ID obrigatório."}, status=status.HTTP_400_BAD_REQUEST)
        escritorio = self._get_escritorio(request)
        try:
            obj = PlanoContas.objects.get(pk=pk, escritorio=escritorio)
        except PlanoContas.DoesNotExist:
            return Response({"detail": "Não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        serializer = PlanoContasSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk=None):
        if not pk:
            return Response({"detail": "ID obrigatório."}, status=status.HTTP_400_BAD_REQUEST)
        escritorio = self._get_escritorio(request)
        try:
            obj = PlanoContas.objects.get(pk=pk, escritorio=escritorio)
        except PlanoContas.DoesNotExist:
            return Response({"detail": "Não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class HistoricoContabilView(APIView):
    authentication_classes = [KeycloakJWTAuthentication]
    permission_classes = [HasUserGMRole]

    def _get_escritorio(self, request):
        esc_id = request.query_params.get("escritorio")
        if esc_id:
            try:
                return Escritorio.objects.get(pk=esc_id)
            except Escritorio.DoesNotExist:
                pass
        return Escritorio.objects.first()

    def get(self, request):
        escritorio = self._get_escritorio(request)
        if not escritorio:
            return Response({"detail": "Escritório não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        qs = HistoricoContabil.objects.filter(escritorio=escritorio).order_by("codigo")
        if not request.query_params.get("todos"):
            qs = qs.filter(ativo=True)
        serializer = HistoricoContabilSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        escritorio = self._get_escritorio(request)
        if not escritorio:
            return Response({"detail": "Escritório não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        serializer = HistoricoContabilSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(escritorio=escritorio)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def patch(self, request, pk=None):
        if not pk:
            return Response({"detail": "ID obrigatório."}, status=status.HTTP_400_BAD_REQUEST)
        escritorio = self._get_escritorio(request)
        try:
            obj = HistoricoContabil.objects.get(pk=pk, escritorio=escritorio)
        except HistoricoContabil.DoesNotExist:
            return Response({"detail": "Não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        serializer = HistoricoContabilSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk=None):
        if not pk:
            return Response({"detail": "ID obrigatório."}, status=status.HTTP_400_BAD_REQUEST)
        escritorio = self._get_escritorio(request)
        try:
            obj = HistoricoContabil.objects.get(pk=pk, escritorio=escritorio)
        except HistoricoContabil.DoesNotExist:
            return Response({"detail": "Não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
