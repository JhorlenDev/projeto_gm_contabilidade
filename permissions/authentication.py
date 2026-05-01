import logging

from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from services.keycloak import (
    KeycloakConfigurationError,
    KeycloakTokenError,
    KeycloakTokenValidator,
    build_principal,
    sync_keycloak_user,
)


logger = logging.getLogger(__name__)


class KeycloakJWTAuthentication(BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if not header:
            logger.info("Keycloak auth ignorada: sem Authorization header path=%s", request.path)
            return None

        if header[0].lower() != b"bearer":
            logger.warning("Authorization header inválido: esquema diferente de Bearer path=%s", request.path)
            return None

        if len(header) == 1:
            logger.warning("Bearer token ausente path=%s", request.path)
            raise AuthenticationFailed("Token ausente no header Authorization.")

        if len(header) > 2:
            logger.warning("Authorization header malformado path=%s", request.path)
            raise AuthenticationFailed("Header Authorization inválido.")

        token = header[1].decode("utf-8")
        validator = KeycloakTokenValidator()

        logger.info("Validando JWT do Keycloak path=%s", request.path)

        try:
            claims = validator.validate(token)
        except (KeycloakConfigurationError, KeycloakTokenError) as exc:
            logger.warning("Falha na validação do JWT path=%s erro=%s", request.path, exc)
            raise AuthenticationFailed(str(exc)) from exc

        logger.info(
            "JWT validado com sucesso path=%s user=%s realm_roles=%s client_roles=%s",
            request.path,
            claims.get("preferred_username") or claims.get("email") or claims.get("sub") or "",
            claims.get("realm_access", {}).get("roles", []),
            (claims.get("resource_access", {}).get(validator.client_id) or {}).get("roles", []),
        )
        principal = build_principal(claims)
        profile = sync_keycloak_user(claims)
        if profile is not None:
            setattr(principal, "profile_id", str(profile.id))

        return principal, claims

    def authenticate_header(self, request):
        return self.keyword
