from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api_views import (
    CertificadoDigitalClienteViewSet,
    ClienteViewSet,
    ComprovantePreviw,
    ContaClienteViewSet,
    EscritorioViewSet,
    ExtratoHistoricoView,
    ExtratoPreviewView,
    ImportacaoExtratoViewSet,
    KeycloakTokenExchangeView,
    PerfilConciliacaoViewSet,
    RegraConciliadorViewSet,
    TransacaoImportadaViewSet,
)
from .views import TesteView


router = DefaultRouter()
router.register(r"clientes", ClienteViewSet, basename="clientes")
router.register(r"contas-clientes", ContaClienteViewSet, basename="contas-clientes")
router.register(r"certificados-clientes", CertificadoDigitalClienteViewSet, basename="certificados-clientes")
router.register(r"escritorios", EscritorioViewSet, basename="escritorios")
router.register(r"conciliador-importacoes", ImportacaoExtratoViewSet, basename="conciliador-importacoes")
router.register(r"conciliador-transacoes", TransacaoImportadaViewSet, basename="conciliador-transacoes")
router.register(r"conciliador-regras", RegraConciliadorViewSet, basename="conciliador-regras")
router.register(r"conciliador-perfis", PerfilConciliacaoViewSet, basename="conciliador-perfis")

urlpatterns = [
    path("", include(router.urls)),
    path("auth/keycloak/token/", KeycloakTokenExchangeView.as_view(), name="keycloak-token-exchange"),
    path("extrato-preview/", ExtratoPreviewView.as_view(), name="extrato-preview"),
    path("comprovante-preview/", ComprovantePreviw.as_view(), name="comprovante-preview"),
    path("extrato-historico/", ExtratoHistoricoView.as_view(), name="extrato-historico"),
    path("extrato-historico/<uuid:pk>/", ExtratoHistoricoView.as_view(), name="extrato-historico-delete"),
    path("teste/", TesteView.as_view(), name="teste"),
]
