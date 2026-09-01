from __future__ import annotations
from django.contrib import messages
from django.contrib.auth import login, logout, get_user_model
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from apps.audit.models import AuditLog
from apps.audit.services.audit_service import AuditService

from django.conf import settings
from django.contrib.auth.decorators import login_required

from django.http import HttpResponseForbidden
from django.views.decorators.http import require_GET

import time
import logging
import secrets
from urllib.parse import urlencode, urlparse

from django.conf import settings
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.adapters.azure_identity import AzureIdentityAdapter
from apps.accounts.contracts import (
    IdentityConfigurationError,
    IdentityProviderError,
    IdentityValidationError,
)
from apps.accounts.services.delegated_token_cache import (
    DelegatedTokenCacheService,
)
from apps.accounts.services.identity_service import IdentityService


logger = logging.getLogger(__name__)


ENTRA_AUTH_FLOW_SESSION_KEY = "entra_auth_code_flow"
ENTRA_AUTH_NEXT_SESSION_KEY = "entra_auth_next"
ENTRA_AUTH_STARTED_AT_SESSION_KEY = "entra_auth_started_at"

DEFAULT_LOGIN_REDIRECT_URL = "/"


def _safe_next_url(
    request: HttpRequest,
    candidate: str | None,
) -> str:
    value = str(candidate or "").strip()

    default_url = str(
        getattr(
            settings,
            "LOGIN_REDIRECT_URL",
            "/",
        )
    )

    if not value:
        return default_url

    parsed = urlparse(value)

    if parsed.scheme or parsed.netloc:
        return default_url

    if not value.startswith("/") or value.startswith("//"):
        return default_url

    return value


def _clear_entra_flow(request: HttpRequest) -> None:
    request.session.pop(
        ENTRA_AUTH_FLOW_SESSION_KEY,
        None,
    )
    request.session.pop(
        ENTRA_AUTH_NEXT_SESSION_KEY,
        None,
    )
    request.session.pop(
        ENTRA_AUTH_STARTED_AT_SESSION_KEY,
        None,
    )


@require_GET
def login_view(request: HttpRequest) -> HttpResponse:
    """
    Renderiza la entrada principal a Invoice Flow.

    Microsoft Entra ID es el mecanismo corporativo.
    El acceso local solo se muestra cuando desarrollo y la
    configuración explícita de dev login están habilitados.
    """

    if request.user.is_authenticated:
        return redirect(
            _safe_next_url(
                request,
                request.GET.get("next"),
            )
        )

    dev_login_enabled = bool(
        settings.DEBUG
        and getattr(
            settings,
            "DEV_LOGIN_ENABLED",
            False,
        )
    )

    context = {
        "entra_auth_enabled": bool(
            getattr(
                settings,
                "ENTRA_AUTH_ENABLED",
                False,
            )
        ),
        "dev_login_enabled": dev_login_enabled,
        "next_url": _safe_next_url(
            request,
            request.GET.get("next"),
        ),
    }

    return render(
        request,
        "accounts/login.html",
        context,
    )

@require_GET
def entra_login_view(
    request: HttpRequest,
) -> HttpResponse:
    """
    Inicia Authorization Code Flow con Microsoft Entra ID.
    """

    if not settings.ENTRA_AUTH_ENABLED:
        messages.error(
            request,
            "La autenticación corporativa no está habilitada.",
        )
        return redirect("accounts:login")

    if request.user.is_authenticated:
        return redirect(
            _safe_next_url(
                request,
                request.GET.get("next"),
            )
        )

    _clear_entra_flow(request)

    next_url = _safe_next_url(
        request,
        request.GET.get("next"),
    )

    state = secrets.token_urlsafe(32)

    try:
        adapter = AzureIdentityAdapter()

        flow = adapter.initiate_auth_code_flow(
            state=state,
        )
    except (
        IdentityConfigurationError,
        IdentityProviderError,
    ) as exc:
        logger.exception(
            "No fue posible iniciar el login con Entra.",
            extra={
                "reason": getattr(
                    exc,
                    "reason",
                    None,
                ),
            },
        )

        messages.error(
            request,
            "No fue posible iniciar el acceso corporativo. "
            "Inténtalo nuevamente.",
        )

        return redirect("accounts:login")

    request.session[
        ENTRA_AUTH_FLOW_SESSION_KEY
    ] = flow

    request.session[
        ENTRA_AUTH_NEXT_SESSION_KEY
    ] = next_url

    request.session[
        ENTRA_AUTH_STARTED_AT_SESSION_KEY
    ] = int(time.time())

    request.session.modified = True

    auth_uri = str(flow.get("auth_uri", "")).strip()

    if not auth_uri:
        _clear_entra_flow(request)

        messages.error(
            request,
            "Microsoft Entra no entregó una URL de acceso válida.",
        )

        return redirect("accounts:login")

    return redirect(auth_uri)


@require_GET
def entra_callback_view(
    request: HttpRequest,
) -> HttpResponse:
    """
    Completa el callback de Entra, autoriza en Django e inicia sesión.
    """

    if not settings.ENTRA_AUTH_ENABLED:
        _clear_entra_flow(request)

        messages.error(
            request,
            "La autenticación corporativa no está habilitada.",
        )

        return redirect("accounts:login")

    auth_code_flow = request.session.pop(
        ENTRA_AUTH_FLOW_SESSION_KEY,
        None,
    )

    next_url = _safe_next_url(
        request,
        request.session.pop(
            ENTRA_AUTH_NEXT_SESSION_KEY,
            None,
        ),
    )

    request.session.pop(
        ENTRA_AUTH_STARTED_AT_SESSION_KEY,
        None,
    )

    auth_response = request.GET.dict()

    try:
        delegated_token_cache = (
            DelegatedTokenCacheService
            .create_empty_cache()
        )

        adapter = AzureIdentityAdapter(
            token_cache=delegated_token_cache,
        )

        identity = adapter.complete_auth_code_flow(
            auth_code_flow=auth_code_flow or {},
            auth_response=auth_response,
        )

        authorization = IdentityService.authorize(
            identity
        )

        django_login(
            request,
            authorization.user,
            backend=(
                "django.contrib.auth.backends."
                "ModelBackend"
            ),
        )

        request.session.cycle_key()

        DelegatedTokenCacheService.save_to_session(
            session=request.session,
            token_cache=delegated_token_cache,
        )

        request.session[
            "identity_provider"
        ] = identity.provider.value

        request.session[
            "identity_revalidation_failures"
        ] = 0

        request.session[
            "identity_external_id"
        ] = identity.external_id

        request.session[
            "identity_tenant_id"
        ] = identity.tenant_id

        request.session[
            "identity_last_validated_at"
        ] = int(time.time())

        request.session[
            "identity_session_started_at"
        ] = int(time.time())

        request.session.set_expiry(
            int(
                settings.INACTIVITY_TIMEOUT_MINUTES
            )
            * 60
        )

        request.session.modified = True

    except IdentityValidationError as exc:
        logger.warning(
            "Identidad Entra rechazada por Django.",
            extra={
                "reason": str(
                    getattr(exc, "reason", "")
                ),
                "metadata": getattr(
                    exc,
                    "metadata",
                    {},
                ),
            },
        )

        django_logout(request)

        messages.error(
            request,
            "Tu cuenta corporativa no tiene acceso autorizado "
            "a Invoice Flow.",
        )

        return redirect("accounts:login")

    except (
        IdentityConfigurationError,
        IdentityProviderError,
    ) as exc:
        logger.exception(
            "Falló el proveedor de identidad.",
            extra={
                "reason": str(
                    getattr(exc, "reason", "")
                ),
            },
        )

        django_logout(request)

        messages.error(
            request,
            "No fue posible validar tu cuenta corporativa. "
            "Inténtalo nuevamente.",
        )

        return redirect("accounts:login")

    except Exception:
        logger.exception(
            "Error inesperado durante el callback de Entra."
        )

        django_logout(request)

        messages.error(
            request,
            "Ocurrió un error durante el inicio de sesión.",
        )

        return redirect("accounts:login")

    messages.success(
        request,
        "Sesión iniciada correctamente.",
    )

    return redirect(next_url)


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    """
    Cierra la sesión local de Django.

    Cuando el cierre global de Microsoft Entra está habilitado,
    redirige también al endpoint corporativo de logout.
    """

    django_logout(request)

    messages.success(
        request,
        "Sesión cerrada correctamente.",
    )

    if not getattr(
        settings,
        "ENTRA_GLOBAL_LOGOUT_ENABLED",
        False,
    ):
        return redirect("accounts:login")

    logout_endpoint = (
        f"{settings.ENTRA_AUTHORITY.rstrip('/')}"
        "/oauth2/v2.0/logout"
    )

    query = urlencode(
        {
            "post_logout_redirect_uri": (
                settings.ENTRA_POST_LOGOUT_REDIRECT_URI
            ),
        }
    )

    return redirect(
        f"{logout_endpoint}?{query}"
    )


def _get_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    return request.META.get("REMOTE_ADDR")


def _register_auth_event(request, event_type, user=None, username_snapshot="", metadata=None):
    AuditService.register_event(
        event_type=event_type,
        user=user,
        username_snapshot=username_snapshot,
        ip_address=_get_client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
        metadata=metadata or {},
    )


@require_GET
def dev_login_view(request):
    """
    Login simulado SOLO para desarrollo.

    No reemplaza Azure AD / Entra ID.
    Bloqueado explícitamente cuando DEBUG=False.
    """

    if not (
        settings.DEBUG
        and getattr(
            settings,
            "DEV_LOGIN_ENABLED",
            False,
        )
    ):
        return HttpResponseForbidden(
            "El acceso local de desarrollo no está habilitado."
        )

    next_url = request.GET.get("next") or "/dashboard/"

    UserModel = get_user_model()

    user, _created = UserModel.objects.get_or_create(
        username="dev.corporate.user",
        defaults={
            "email": "dev.corporate.user@example.com",
            "first_name": "Dev",
            "last_name": "Corporate User",
            "is_active": True,
            "is_staff": False,
            "is_superuser": False,
        },
    )



    if not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])

    login(request, user)

    now = timezone.now().isoformat()
    request.session["login_at"] = now
    request.session["last_activity_at"] = now
    request.session["next_url"] = next_url
    request.session.modified = True

    request.session[
        "identity_provider"
    ] = "local"

    _register_auth_event(
        request=request,
        event_type=AuditLog.LOGIN_SUCCESS,
        user=user,
        username_snapshot=user.get_username(),
        metadata={
            "source": "dev_login",
            "mode": "DEV_ONLY",
            "next_url": next_url,
        },
    )

    messages.success(request, "Sesión de desarrollo iniciada correctamente.")
    return redirect(next_url)