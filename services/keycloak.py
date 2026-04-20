from __future__ import annotations

import logging
import json
import textwrap
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
import requests
from django.conf import settings
from django.utils import timezone
from jwt import InvalidTokenError
from jwt.algorithms import RSAAlgorithm


logger = logging.getLogger(__name__)


class KeycloakConfigurationError(RuntimeError):
    """Raised when Keycloak settings are incomplete."""


class KeycloakTokenError(RuntimeError):
    """Raised when a JWT cannot be validated."""


@dataclass
class KeycloakPrincipal:
    claims: dict[str, Any]
    roles: tuple[str, ...]
    username: str = ""
    email: str = ""
    sub: str = ""

    def __post_init__(self) -> None:
        self.is_authenticated = True
        self.is_anonymous = False

    def __str__(self) -> str:
        return self.username or self.email or self.sub or "keycloak-user"


def extract_roles(claims: dict[str, Any], client_id: str = "") -> list[str]:
    roles: list[str] = []

    realm_access = claims.get("realm_access") or {}
    roles.extend(realm_access.get("roles") or [])

    resource_access = claims.get("resource_access") or {}
    if client_id:
        client_roles = (resource_access.get(client_id) or {}).get("roles") or []
        roles.extend(client_roles)
    else:
        for client_roles in resource_access.values():
            roles.extend((client_roles or {}).get("roles") or [])

    seen: set[str] = set()
    unique_roles: list[str] = []
    for role in roles:
        if role not in seen:
            seen.add(role)
            unique_roles.append(role)
    return unique_roles


def normalize_role(role: str) -> str:
    return str(role or "").strip().upper().replace("_", "-")


def build_principal(claims: dict[str, Any]) -> KeycloakPrincipal:
    roles = tuple(extract_roles(claims, settings.KEYCLOAK_CLIENT_ID))
    username = claims.get("preferred_username") or claims.get("email") or claims.get("sub") or ""
    email = claims.get("email") or ""
    sub = claims.get("sub") or ""
    return KeycloakPrincipal(claims=claims, roles=roles, username=username, email=email, sub=sub)


def sync_keycloak_user(claims: dict[str, Any]):
    sub = str(claims.get("sub") or "").strip()
    if not sub:
        logger.warning("JWT sem sub; não foi possível sincronizar usuário Keycloak.")
        return None

    try:
        from app.models import KeycloakUser

        nome = (
            claims.get("name")
            or claims.get("preferred_username")
            or claims.get("email")
            or sub
        )
        email = str(claims.get("email") or "").strip()
        roles = extract_roles(claims, settings.KEYCLOAK_CLIENT_ID)

        user, created = KeycloakUser.objects.update_or_create(
            sub=sub,
            defaults={
                "nome": str(nome).strip(),
                "email": email,
                "roles": roles,
                "last_seen_at": timezone.now(),
            },
        )

        logger.info(
            "Usuário Keycloak %s no banco sub=%s email=%s roles=%s",
            "criado" if created else "atualizado",
            user.sub,
            user.email,
            roles,
        )
        return user
    except Exception as exc:  # pragma: no cover - safeguard around sync side-effect
        logger.exception("Falha ao sincronizar usuário Keycloak no banco.")
        return None


def _normalize_public_key(public_key: str) -> str:
    public_key = public_key.strip()
    if public_key.startswith("-----BEGIN"):
        return public_key

    compact = "".join(public_key.split())
    wrapped = "\n".join(textwrap.wrap(compact, 64))
    return f"-----BEGIN PUBLIC KEY-----\n{wrapped}\n-----END PUBLIC KEY-----"


@lru_cache(maxsize=4)
def _fetch_jwks(jwks_url: str) -> dict[str, Any]:
    response = requests.get(jwks_url, timeout=5)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or "keys" not in payload:
        raise KeycloakTokenError("JWKS inválido.")
    return payload


class KeycloakTokenValidator:
    def __init__(self) -> None:
        self.realm_url = settings.KEYCLOAK_REALM_URL
        self.client_id = settings.KEYCLOAK_CLIENT_ID
        self.jwks_url = settings.KEYCLOAK_JWKS_URL
        self.public_key = settings.KEYCLOAK_PUBLIC_KEY
        self.issuer = settings.KEYCLOAK_ISSUER or self.realm_url
        self.algorithm = settings.KEYCLOAK_ALGORITHM
        self.verify_audience = settings.KEYCLOAK_VERIFY_AUDIENCE

    def validate(self, token: str) -> dict[str, Any]:
        header = self._get_header(token)
        logger.info(
            "Validando token JWT kid=%s issuer=%s client_id=%s",
            header.get("kid"),
            self.issuer,
            self.client_id,
        )
        signing_key = self._get_signing_key(header)

        decode_kwargs: dict[str, Any] = {
            "key": signing_key,
            "algorithms": [self.algorithm],
            "options": {"verify_aud": False},
        }
        if self.issuer:
            decode_kwargs["issuer"] = self.issuer

        try:
            claims = jwt.decode(token, **decode_kwargs)
        except InvalidTokenError as exc:
            logger.warning("JWT rejeitado pelo decoder: %s", exc)
            raise KeycloakTokenError("Token JWT inválido.") from exc

        self._validate_audience(claims)
        logger.info(
            "JWT decodificado aud=%s azp=%s sub=%s",
            claims.get("aud"),
            claims.get("azp"),
            claims.get("sub"),
        )
        return claims

    def _get_header(self, token: str) -> dict[str, Any]:
        try:
            return jwt.get_unverified_header(token)
        except InvalidTokenError as exc:
            raise KeycloakTokenError("Cabeçalho JWT inválido.") from exc

    def _get_signing_key(self, header: dict[str, Any]) -> Any:
        if self.jwks_url:
            kid = header.get("kid")
            if not kid:
                logger.warning("JWT sem kid no cabeçalho.")
                raise KeycloakTokenError("JWT sem kid no cabeçalho.")

            logger.info("Buscando chave JWKS kid=%s url=%s", kid, self.jwks_url)
            jwks = self._load_jwks(self.jwks_url)
            jwk = self._find_jwk(jwks, kid)
            if jwk is None:
                logger.warning("KID não encontrado no cache JWKS, recarregando kid=%s", kid)
                _fetch_jwks.cache_clear()
                jwks = self._load_jwks(self.jwks_url)
                jwk = self._find_jwk(jwks, kid)

            if jwk is None:
                logger.error("Chave JWKS não encontrada para kid=%s", kid)
                raise KeycloakTokenError("Chave pública não encontrada no JWKS.")

            return RSAAlgorithm.from_jwk(json.dumps(jwk))

        if self.public_key:
            logger.info("Usando public key estática para validar JWT.")
            return _normalize_public_key(self.public_key)

        raise KeycloakConfigurationError(
            "Configure KEYCLOAK_JWKS_URL ou KEYCLOAK_PUBLIC_KEY para validar o JWT."
        )

    @staticmethod
    def _find_jwk(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
        for jwk in jwks.get("keys", []):
            if jwk.get("kid") == kid:
                return jwk
        return None

    @staticmethod
    def _load_jwks(jwks_url: str) -> dict[str, Any]:
        return _fetch_jwks(jwks_url)

    def _validate_audience(self, claims: dict[str, Any]) -> None:
        if not self.verify_audience or not self.client_id:
            return

        audience = claims.get("aud")
        azp = claims.get("azp")

        if isinstance(audience, str):
            audience_values = {audience}
        elif isinstance(audience, (list, tuple, set)):
            audience_values = {str(value) for value in audience}
        else:
            audience_values = set()

        if self.client_id not in audience_values and azp != self.client_id:
            logger.warning(
                "Audience inválida token aud=%s azp=%s client_id=%s",
                claims.get("aud"),
                azp,
                self.client_id,
            )
            raise KeycloakTokenError("Token não destinado ao client configurado.")

        logger.info("Audience validada com sucesso para client_id=%s", self.client_id)
