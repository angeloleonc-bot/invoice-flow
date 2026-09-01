from __future__ import annotations

from dataclasses import dataclass

import requests

from django.conf import settings

from apps.accounts.adapters.azure_identity import (
    AzureIdentityAdapter,
)
from apps.accounts.services.delegated_token_cache import (
    DelegatedTokenCacheError,
    DelegatedTokenCacheService,
)


GRAPH_SEND_MAIL_URL = (
    "https://graph.microsoft.com/v1.0/me/sendMail"
)

GRAPH_TIMEOUT_SECONDS = 15


class DelegatedGraphError(Exception):
    pass


class DelegatedGraphUnavailableError(
    DelegatedGraphError
):
    """
    No existe una sesión delegada utilizable.
    """


class DelegatedGraphAuthenticationError(
    DelegatedGraphError
):
    """
    Microsoft exige volver a autenticar al usuario.
    """


class DelegatedGraphProviderError(
    DelegatedGraphError
):
    """
    Error inesperado desde Microsoft Graph.
    """


@dataclass(frozen=True)
class DelegatedAccessTokenResult:
    access_token: str
    account_username: str


@dataclass(frozen=True)
class GraphMailSendResult:
    status_code: int
    request_id: str


class DelegatedGraphService:
    """
    Cliente de infraestructura para Microsoft Graph delegado.

    IMPORTANTE:
    - no usa permisos app-only para correo;
    - no persiste tokens en modelos;
    - no registra access_token;
    - no envía correo todavía en esta etapa;
    - requiere cache delegado cifrado en sesión.
    """

    REQUIRED_SCOPE = "Mail.Send"

    @classmethod
    def acquire_access_token(
        cls,
        *,
        request,
    ) -> DelegatedAccessTokenResult:

        if not request.user.is_authenticated:
            raise DelegatedGraphUnavailableError(
                "El usuario no está autenticado."
            )

        identity_provider = str(
            request.session.get(
                "identity_provider",
                "",
            )
            or ""
        ).strip().lower()

        if identity_provider not in {
            "microsoft_entra_id",
            "azure_ad",
            "entra",
        }:
            raise DelegatedGraphUnavailableError(
                "La sesión actual no corresponde "
                "a una autenticación corporativa de Microsoft."
            )

        if (
            cls.REQUIRED_SCOPE
            not in settings.ENTRA_SCOPES
        ):
            raise DelegatedGraphUnavailableError(
                "Mail.Send no está configurado."
            )

        if not (
            DelegatedTokenCacheService
            .encryption_enabled()
        ):
            raise DelegatedGraphUnavailableError(
                "El cache delegado seguro no está "
                "habilitado en este entorno."
            )

        try:
            token_cache = (
                DelegatedTokenCacheService
                .load_from_session(
                    request.session
                )
            )
        except DelegatedTokenCacheError as exc:
            raise DelegatedGraphAuthenticationError(
                "No fue posible restaurar "
                "la sesión delegada de Microsoft."
            ) from exc

        adapter = AzureIdentityAdapter(
            token_cache=token_cache,
        )

        accounts = adapter.client.get_accounts()

        if not accounts:
            raise DelegatedGraphAuthenticationError(
                "No existe una cuenta Microsoft "
                "en el cache delegado. "
                "Debe iniciar sesión nuevamente."
            )

        expected_email = str(
            getattr(
                request.user,
                "email",
                "",
            )
            or ""
        ).strip().lower()

        account = cls._select_account(
            accounts=accounts,
            expected_email=expected_email,
        )

        try:
            result = (
                adapter.client
                .acquire_token_silent(
                    scopes=list(
                        settings.ENTRA_SCOPES
                    ),
                    account=account,
                )
            )
        except (
            ValueError,
            RuntimeError,
        ) as exc:
            raise DelegatedGraphProviderError(
                "No fue posible obtener un token "
                "delegado de Microsoft."
            ) from exc

        if not result:
            raise DelegatedGraphAuthenticationError(
                "La sesión delegada ya no puede "
                "renovarse silenciosamente. "
                "Debe iniciar sesión nuevamente."
            )

        access_token = str(
            result.get(
                "access_token",
                "",
            )
            or ""
        ).strip()

        if not access_token:
            error_code = str(
                result.get(
                    "error",
                    "",
                )
                or ""
            ).strip()

            raise DelegatedGraphAuthenticationError(
                "Microsoft no entregó un token "
                "delegado válido"
                + (
                    f" ({error_code})."
                    if error_code
                    else "."
                )
            )

        # MSAL pudo refrescar el cache.
        # Persistimos nuevamente sólo de forma cifrada.
        DelegatedTokenCacheService.save_to_session(
            session=request.session,
            token_cache=token_cache,
        )

        username = str(
            account.get(
                "username",
                "",
            )
            or ""
        ).strip()

        return DelegatedAccessTokenResult(
            access_token=access_token,
            account_username=username,
        )

    @classmethod
    def _select_account(
        cls,
        *,
        accounts,
        expected_email: str,
    ):

        if expected_email:
            for account in accounts:
                username = str(
                    account.get(
                        "username",
                        "",
                    )
                    or ""
                ).strip().lower()

                if username == expected_email:
                    return account

        if len(accounts) == 1:
            return accounts[0]

        raise DelegatedGraphAuthenticationError(
            "No fue posible determinar de forma "
            "segura qué cuenta Microsoft corresponde "
            "al usuario autenticado."
        )


class GraphMailTransport:
    """
    Transporte HTTP de correo.

    La función real está implementada para poder probar
    su contrato con mocks, pero NO está conectada todavía
    a ningún endpoint/view de Invoice Flow.
    """

    @classmethod
    def send_mail(
        cls,
        *,
        access_token: str,
        payload: dict,
        http_session=None,
    ) -> None:

        token = str(
            access_token
            or ""
        ).strip()

        if not token:
            raise DelegatedGraphAuthenticationError(
                "No existe access token."
            )

        session = (
            http_session
            or requests.Session()
        )

        try:
            response = session.post(
                GRAPH_SEND_MAIL_URL,
                headers={
                    "Authorization": (
                        f"Bearer {token}"
                    ),
                    "Content-Type": (
                        "application/json"
                    ),
                    "Accept": (
                        "application/json"
                    ),
                },
                json=payload,
                timeout=GRAPH_TIMEOUT_SECONDS,
            )

        except requests.RequestException as exc:
            raise DelegatedGraphProviderError(
                "No fue posible conectar con "
                "Microsoft Graph."
            ) from exc

        # /me/sendMail responde normalmente 202 Accepted.
        if response.status_code == 202:
            request_id = str(
                response.headers.get("request-id")
                or response.headers.get("client-request-id")
                or ""
            ).strip()

            return GraphMailSendResult(
                status_code=response.status_code,
                request_id=request_id,
            )

        request_id = (
            response.headers.get(
                "request-id"
            )
            or response.headers.get(
                "client-request-id"
            )
            or ""
        )

        raise DelegatedGraphProviderError(
            "Microsoft Graph rechazó el envío "
            f"(HTTP {response.status_code})"
            + (
                f", request-id {request_id}."
                if request_id
                else "."
            )
        )
