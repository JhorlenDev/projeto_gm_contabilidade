from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api_views import ClienteViewSet, EscritorioViewSet, ImportacaoExtratoViewSet, RegraConciliadorViewSet, TransacaoImportadaViewSet
from .views import TesteView


router = DefaultRouter()
router.register(r"clientes", ClienteViewSet, basename="clientes")
router.register(r"escritorios", EscritorioViewSet, basename="escritorios")
router.register(r"conciliador-importacoes", ImportacaoExtratoViewSet, basename="conciliador-importacoes")
router.register(r"conciliador-transacoes", TransacaoImportadaViewSet, basename="conciliador-transacoes")
router.register(r"conciliador-regras", RegraConciliadorViewSet, basename="conciliador-regras")

urlpatterns = [
    path("", include(router.urls)),
    path("teste/", TesteView.as_view(), name="teste"),
]
