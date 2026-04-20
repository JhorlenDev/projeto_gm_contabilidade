import logging

from django.conf import settings
from rest_framework.permissions import IsAuthenticated

from services.keycloak import extract_roles, normalize_role


logger = logging.getLogger(__name__)


class HasUserGMRole(IsAuthenticated):
    message = "Acesso negado. A role USER-GM é obrigatória."

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            logger.warning("Permissão negada: usuário não autenticado path=%s", getattr(request, "path", ""))
            return False

        roles = getattr(request.user, "roles", None)
        if roles is None and isinstance(request.auth, dict):
            roles = extract_roles(request.auth, settings.KEYCLOAK_CLIENT_ID)

        required_role = normalize_role("USER-GM")
        normalized_roles = [normalize_role(role) for role in (roles or [])]
        allowed = any(role == required_role for role in normalized_roles)

        if allowed:
            logger.info(
                "Permissão concedida USER-GM path=%s user=%s roles=%s",
                getattr(request, "path", ""),
                getattr(request.user, "username", "") or getattr(request.user, "sub", ""),
                normalized_roles,
            )
        else:
            logger.warning(
                "Permissão negada USER-GM path=%s user=%s roles=%s required=%s",
                getattr(request, "path", ""),
                getattr(request.user, "username", "") or getattr(request.user, "sub", ""),
                normalized_roles,
                required_role,
            )

        return allowed
