from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import msal
import requests
from django.conf import settings

from apps.accounts.contracts import (
    ExternalIdentity,
    IdentityConfigurationError,
    IdentityFailureReason,
    IdentityProvider,
    IdentityProviderError,
    IdentityValidationError,
)


logger = logging.getLogger(__name__)


class AzureIdentityAdapter:
    """
    Adapter técnico para Microsoft Entra ID.

    Responsabilidades:
    - Construir y completar Authorization Code Flow mediante MSAL.
    - Validar claims esenciales del ID token.
    - Resolver grupos desde el token o Microsoft Graph.
    - Normalizar el resultado como ExternalIdentity.

    No crea usuarios Django, no asigna roles y no inicia sesiones.
    """

    GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
    GRAPH_GROUPS_URL = (
        GRAPH_BASE_URL
        + "/me/transitiveMemberOf/microsoft.graph.group"
        + "?$select=id&$top=999"
    )

    GRAPH_ALLOWED_HOST = "graph.microsoft.com"
    REQUEST_TIMEOUT_SECONDS = 10

    def __init__(
        self,
        *,
        token_cache: msal.TokenCache | None = None,
        http_session: requests.Session | None = None,
    ):
        self._validate_configuration()

        self.token_cache = token_cache or msal.SerializableTokenCache()
        self.http_session = http_session or requests.Session()

        self.client = msal.ConfidentialClientApplication(
            client_id=settings.ENTRA_CLIENT_ID,
            client_credential=settings.ENTRA_CLIENT_SECRET,
            authority=settings.ENTRA_AUTHORITY,
            token_cache=self.token_cache,
        )

    def initiate_auth_code_flow(
        self,
        *,
        state: str | None = None,
        login_hint: str | None = None,
    ) -> dict[str, Any]:
        """
        Inicia el Authorization Code Flow.

        El diccionario retornado debe guardarse completo en la sesión Django
        y utilizarse una sola vez en el callback.
        """

        try:
            flow = self.client.initiate_auth_code_flow(
                scopes=list(settings.ENTRA_SCOPES),
                redirect_uri=settings.ENTRA_REDIRECT_URI,
                state=state,
                login_hint=login_hint,
                response_mode="query",
            )
        except (ValueError, RuntimeError) as exc:
            raise IdentityProviderError(
                "No fue posible iniciar la autenticación con Microsoft Entra ID.",
                metadata={
                    "operation": "initiate_auth_code_flow",
                },
            ) from exc

        auth_uri = flow.get("auth_uri")
        flow_state = flow.get("state")

        if not auth_uri or not flow_state:
            raise IdentityProviderError(
                "Microsoft Entra ID no entregó un flujo de autorización válido.",
                metadata={
                    "operation": "initiate_auth_code_flow",
                },
            )

        return flow

    def complete_auth_code_flow(
        self,
        *,
        auth_code_flow: Mapping[str, Any],
        auth_response: Mapping[str, str],
    ) -> ExternalIdentity:
        """
        Completa el callback de Microsoft y devuelve una identidad normalizada.

        auth_code_flow:
            Diccionario original generado por initiate_auth_code_flow().

        auth_response:
            Parámetros GET recibidos en el callback.
        """

        if not auth_code_flow:
            raise IdentityValidationError(
                "No existe un flujo de autenticación pendiente.",
                reason=IdentityFailureReason.INVALID_STATE,
            )

        if not auth_response:
            raise IdentityValidationError(
                "La respuesta de autenticación está vacía.",
                reason=IdentityFailureReason.INVALID_TOKEN,
            )

        try:
            result = self.client.acquire_token_by_auth_code_flow(
                dict(auth_code_flow),
                dict(auth_response),
                scopes=list(settings.ENTRA_SCOPES),
            )
        except ValueError as exc:
            # MSAL puede generar ValueError ante state o nonce inválidos.
            raise IdentityValidationError(
                "La respuesta de Microsoft Entra ID no superó la validación.",
                reason=IdentityFailureReason.INVALID_STATE,
            ) from exc
        except RuntimeError as exc:
            raise IdentityProviderError(
                "No fue posible completar la autenticación con Microsoft Entra ID.",
                metadata={
                    "operation": "acquire_token_by_auth_code_flow",
                },
            ) from exc

        self._raise_for_token_error(result)

        claims = result.get("id_token_claims")
        access_token = result.get("access_token")

        if not isinstance(claims, Mapping):
            raise IdentityValidationError(
                "Microsoft Entra ID no entregó claims de identidad válidos.",
                reason=IdentityFailureReason.MISSING_REQUIRED_CLAIMS,
            )

        validated_claims = dict(claims)
        self._validate_claims(validated_claims)

        group_ids = self._resolve_group_ids(
            claims=validated_claims,
            access_token=access_token,
        )

        return self._build_external_identity(
            claims=validated_claims,
            group_ids=group_ids,
        )

    def _validate_configuration(self) -> None:
        required_settings = {
            "ENTRA_TENANT_ID": getattr(settings, "ENTRA_TENANT_ID", ""),
            "ENTRA_CLIENT_ID": getattr(settings, "ENTRA_CLIENT_ID", ""),
            "ENTRA_CLIENT_SECRET": getattr(
                settings,
                "ENTRA_CLIENT_SECRET",
                "",
            ),
            "ENTRA_AUTHORITY": getattr(settings, "ENTRA_AUTHORITY", ""),
            "ENTRA_REDIRECT_URI": getattr(
                settings,
                "ENTRA_REDIRECT_URI",
                "",
            ),
        }

        missing = [
            name
            for name, value in required_settings.items()
            if not str(value).strip()
        ]

        if missing:
            raise IdentityConfigurationError(
                "La configuración de Microsoft Entra ID está incompleta.",
                metadata={
                    "missing_settings": missing,
                },
            )

        scopes = getattr(settings, "ENTRA_SCOPES", ())

        if not scopes:
            raise IdentityConfigurationError(
                "ENTRA_SCOPES no puede estar vacío.",
                metadata={
                    "missing_settings": ["ENTRA_SCOPES"],
                },
            )

        redirect_uri = urlparse(settings.ENTRA_REDIRECT_URI)

        if redirect_uri.scheme not in {"http", "https"}:
            raise IdentityConfigurationError(
                "ENTRA_REDIRECT_URI debe usar HTTP o HTTPS."
            )

        if (
            redirect_uri.scheme != "https"
            and redirect_uri.hostname not in {"localhost", "127.0.0.1"}
        ):
            raise IdentityConfigurationError(
                "ENTRA_REDIRECT_URI debe usar HTTPS fuera del entorno local."
            )

    def _validate_claims(self, claims: dict[str, Any]) -> None:
        required_claims = {
            "oid",
            "tid",
            "aud",
            "iss",
            "exp",
        }

        missing_claims = sorted(
            claim_name
            for claim_name in required_claims
            if not claims.get(claim_name)
        )

        if missing_claims:
            raise IdentityValidationError(
                "El token no contiene todos los claims obligatorios.",
                reason=IdentityFailureReason.MISSING_REQUIRED_CLAIMS,
                metadata={
                    "missing_claims": missing_claims,
                },
            )

        tenant_id = str(claims["tid"]).strip().lower()
        configured_tenant_id = settings.ENTRA_TENANT_ID.strip().lower()

        if tenant_id != configured_tenant_id:
            raise IdentityValidationError(
                "La identidad pertenece a un tenant no autorizado.",
                reason=IdentityFailureReason.INVALID_TENANT,
                metadata={
                    "received_tenant_id": tenant_id,
                },
            )

        audience = claims["aud"]

        if isinstance(audience, str):
            valid_audience = (
                audience.strip().lower()
                == settings.ENTRA_CLIENT_ID.strip().lower()
            )
        elif isinstance(audience, (list, tuple, set)):
            valid_audience = (
                settings.ENTRA_CLIENT_ID.strip().lower()
                in {
                    str(value).strip().lower()
                    for value in audience
                }
            )
        else:
            valid_audience = False

        if not valid_audience:
            raise IdentityValidationError(
                "La audiencia del token no corresponde a Invoice Flow.",
                reason=IdentityFailureReason.INVALID_AUDIENCE,
            )

        expected_issuer = (
            f"https://login.microsoftonline.com/"
            f"{settings.ENTRA_TENANT_ID}/v2.0"
        ).lower()

        received_issuer = str(claims["iss"]).strip().rstrip("/").lower()
        normalized_expected_issuer = expected_issuer.rstrip("/")

        if received_issuer != normalized_expected_issuer:
            raise IdentityValidationError(
                "El emisor del token no corresponde al tenant configurado.",
                reason=IdentityFailureReason.INVALID_TENANT,
                metadata={
                    "received_issuer": received_issuer,
                },
            )

    def _resolve_group_ids(
        self,
        *,
        claims: dict[str, Any],
        access_token: str | None,
    ) -> frozenset[str]:
        groups = claims.get("groups")

        if isinstance(groups, (list, tuple, set)):
            return self._normalize_group_ids(groups)

        if self._has_group_overage(claims):
            if not access_token:
                raise IdentityProviderError(
                    "Se detectó group overage, pero no existe un access token "
                    "para consultar Microsoft Graph.",
                    reason=(
                        IdentityFailureReason
                        .GROUP_OVERAGE_RESOLUTION_FAILED
                    ),
                    metadata={
                        "operation": "resolve_group_overage",
                    },
                )

            return self._fetch_graph_group_ids(access_token)

        return frozenset()

    @staticmethod
    def _has_group_overage(claims: Mapping[str, Any]) -> bool:
        if claims.get("hasgroups") is True:
            return True

        claim_names = claims.get("_claim_names")

        return (
            isinstance(claim_names, Mapping)
            and "groups" in claim_names
        )

    def _fetch_graph_group_ids(
        self,
        access_token: str,
    ) -> frozenset[str]:
        """
        Recupera pertenencias transitivas y sigue @odata.nextLink.

        Se aceptan exclusivamente enlaces HTTPS del host graph.microsoft.com.
        """

        group_ids: set[str] = set()
        next_url: str | None = self.GRAPH_GROUPS_URL

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "ConsistencyLevel": "eventual",
        }

        while next_url:
            self._validate_graph_url(next_url)

            try:
                response = self.http_session.get(
                    next_url,
                    headers=headers,
                    timeout=self.REQUEST_TIMEOUT_SECONDS,
                )
            except requests.RequestException as exc:
                raise IdentityProviderError(
                    "Microsoft Graph no está disponible.",
                    reason=(
                        IdentityFailureReason
                        .GROUP_OVERAGE_RESOLUTION_FAILED
                    ),
                    metadata={
                        "operation": "fetch_graph_groups",
                    },
                ) from exc

            if response.status_code != 200:
                correlation_id = (
                    response.headers.get("request-id")
                    or response.headers.get("client-request-id")
                )

                raise IdentityProviderError(
                    "Microsoft Graph rechazó la consulta de grupos.",
                    reason=(
                        IdentityFailureReason
                        .GROUP_OVERAGE_RESOLUTION_FAILED
                    ),
                    metadata={
                        "operation": "fetch_graph_groups",
                        "status_code": response.status_code,
                        "correlation_id": correlation_id,
                    },
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise IdentityProviderError(
                    "Microsoft Graph entregó una respuesta inválida.",
                    reason=(
                        IdentityFailureReason
                        .GROUP_OVERAGE_RESOLUTION_FAILED
                    ),
                    metadata={
                        "operation": "fetch_graph_groups",
                    },
                ) from exc

            values = payload.get("value", [])

            if not isinstance(values, list):
                raise IdentityProviderError(
                    "La colección de grupos de Microsoft Graph es inválida.",
                    reason=(
                        IdentityFailureReason
                        .GROUP_OVERAGE_RESOLUTION_FAILED
                    ),
                    metadata={
                        "operation": "fetch_graph_groups",
                    },
                )

            for item in values:
                if not isinstance(item, Mapping):
                    continue

                group_id = str(item.get("id", "")).strip().lower()

                if group_id:
                    group_ids.add(group_id)

            raw_next_url = payload.get("@odata.nextLink")
            next_url = (
                str(raw_next_url).strip()
                if raw_next_url
                else None
            )

        return frozenset(group_ids)

    def _validate_graph_url(self, url: str) -> None:
        parsed_url = urlparse(url)

        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname != self.GRAPH_ALLOWED_HOST
        ):
            raise IdentityProviderError(
                "Microsoft Graph entregó una URL de paginación no permitida.",
                reason=(
                    IdentityFailureReason
                    .GROUP_OVERAGE_RESOLUTION_FAILED
                ),
                metadata={
                    "operation": "validate_graph_pagination",
                },
            )

    def _build_external_identity(
        self,
        *,
        claims: dict[str, Any],
        group_ids: frozenset[str],
    ) -> ExternalIdentity:
        external_id = str(claims.get("oid", "")).strip().lower()
        tenant_id = str(claims.get("tid", "")).strip().lower()

        username = str(
            claims.get("preferred_username")
            or claims.get("upn")
            or ""
        ).strip().lower()

        email = str(
            claims.get("email")
            or username
        ).strip().lower()

        display_name = str(
            claims.get("name")
            or ""
        ).strip()

        first_name = str(
            claims.get("given_name")
            or ""
        ).strip()

        last_name = str(
            claims.get("family_name")
            or ""
        ).strip()

        if not external_id or not tenant_id:
            raise IdentityValidationError(
                "No fue posible identificar de forma estable al usuario.",
                reason=IdentityFailureReason.MISSING_REQUIRED_CLAIMS,
            )

        if not username:
            raise IdentityValidationError(
                "La identidad no contiene un nombre de usuario utilizable.",
                reason=IdentityFailureReason.MISSING_REQUIRED_CLAIMS,
                metadata={
                    "missing_claims": [
                        "preferred_username",
                    ],
                },
            )

        if not email:
            raise IdentityValidationError(
                "La identidad no contiene un correo utilizable.",
                reason=IdentityFailureReason.MISSING_REQUIRED_CLAIMS,
                metadata={
                    "missing_claims": ["email"],
                },
            )

        safe_claims = {
            key: value
            for key, value in claims.items()
            if key not in {
                "aio",
                "at_hash",
                "c_hash",
                "rh",
            }
        }

        return ExternalIdentity(
            provider=IdentityProvider.MICROSOFT_ENTRA_ID,
            external_id=external_id,
            tenant_id=tenant_id,
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            display_name=display_name,
            group_ids=group_ids,
            claims=safe_claims,
        )

    @staticmethod
    def _normalize_group_ids(
        group_ids: list[Any] | tuple[Any, ...] | set[Any],
    ) -> frozenset[str]:
        return frozenset(
            str(group_id).strip().lower()
            for group_id in group_ids
            if str(group_id).strip()
        )

    @staticmethod
    def _raise_for_token_error(result: Mapping[str, Any]) -> None:
        if result.get("access_token") and result.get("id_token_claims"):
            return

        error_code = str(
            result.get("error")
            or "unknown_error"
        ).strip()

        correlation_id = str(
            result.get("correlation_id")
            or ""
        ).strip()

        # No almacenamos error_description porque puede incluir información
        # sensible entregada por el proveedor.
        metadata = {
            "provider_error": error_code,
        }

        if correlation_id:
            metadata["correlation_id"] = correlation_id

        reason = IdentityFailureReason.INVALID_TOKEN

        if error_code in {
            "temporarily_unavailable",
            "server_error",
        }:
            reason = IdentityFailureReason.PROVIDER_UNAVAILABLE

        if error_code in {
            "invalid_grant",
            "interaction_required",
        }:
            reason = IdentityFailureReason.INVALID_TOKEN

        raise IdentityProviderError(
            "Microsoft Entra ID rechazó la autenticación.",
            reason=reason,
            metadata=metadata,
        )