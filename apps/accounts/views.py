from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from apps.audit.models import AuditLog
from apps.audit.services.audit_service import AuditService


AUTH_MESSAGE_MAP = {
    "session_expired": "Tu sesión expiró. Inicia sesión nuevamente.",
    "invalid_identity": "No fue posible validar tu identidad corporativa.",
    "access_revoked": "Tu acceso fue revocado o no se encuentra habilitado.",
    "token_expired": "El token de autenticación expiró. Inicia sesión nuevamente.",
    "logout": "Sesión cerrada correctamente.",
}


def _safe_next_url(request):
    next_url = request.GET.get("next") or request.POST.get("next") or ""

    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url

    return ""


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


def login_view(request):
    message_code = request.GET.get("message")

    if message_code in AUTH_MESSAGE_MAP:
        messages.info(request, AUTH_MESSAGE_MAP[message_code])

    context = {
        "next": _safe_next_url(request),
    }

    return render(request, "accounts/login.html", context)


def logout_view(request):
    user = request.user if request.user.is_authenticated else None
    username_snapshot = user.get_username() if user else ""

    _register_auth_event(
        request=request,
        event_type=AuditLog.LOGOUT,
        user=user,
        username_snapshot=username_snapshot,
        metadata={"source": "local_logout"},
    )

    logout(request)

    login_url = reverse("accounts:login")
    return redirect(f"{login_url}?message=logout")


def auth_start_view(request):
    next_url = _safe_next_url(request)

    _register_auth_event(
        request=request,
        event_type=AuditLog.IDENTITY_ERROR,
        metadata={
            "source": "auth_start_placeholder",
            "reason": "corporate_auth_not_implemented_yet",
            "next": next_url,
        },
    )

    messages.info(
        request,
        "La autenticación corporativa será habilitada en una etapa posterior.",
    )
    login_url = reverse("accounts:login")

    if next_url:
        return redirect(f"{login_url}?next={next_url}")

    return redirect(login_url)


def callback_placeholder_view(request):
    _register_auth_event(
        request=request,
        event_type=AuditLog.IDENTITY_ERROR,
        metadata={
            "source": "callback_placeholder",
            "reason": "callback_not_implemented_yet",
        },
    )

    messages.warning(
        request,
        "Callback placeholder: esta ruta no autentica usuarios todavía.",
    )

    login_url = reverse("accounts:login")
    return redirect(login_url)