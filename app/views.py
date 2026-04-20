import logging

from django.shortcuts import render
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Cliente, Escritorio
from permissions.authentication import KeycloakJWTAuthentication
from permissions.permissions import HasUserGMRole


logger = logging.getLogger(__name__)


def login_page(request):
    logger.info("Renderizando tela de login path=%s", request.path)
    return render(request, "app/login.html")


def panel_page(request):
    logger.info("Renderizando painel path=%s", request.path)
    initial_view = request.GET.get("view", "dashboard")
    if initial_view not in {"dashboard", "clientes", "conciliador"}:
        initial_view = "dashboard"

    context = {
        "initial_view": initial_view,
        "clientes_dropdown": Cliente.objects.all().only("id", "nome", "cpf_cnpj").order_by("nome"),
        "escritorios_dropdown": Escritorio.objects.all().only("id", "nome", "cnpj").order_by("nome"),
    }

    return render(request, "app/panel.html", context)


class TesteView(APIView):
    authentication_classes = [KeycloakJWTAuthentication]
    permission_classes = [HasUserGMRole]

    def get(self, request):
        user = request.user
        roles = list(getattr(user, "roles", []) or [])

        return Response(
            {
                "detail": "acesso permitido",
                "usuario": getattr(user, "username", "") or getattr(user, "email", "") or getattr(user, "sub", ""),
                "roles": roles,
            }
        )
