from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


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


def login_view(request):
    message_code = request.GET.get("message")

    if message_code in AUTH_MESSAGE_MAP:
        messages.info(request, AUTH_MESSAGE_MAP[message_code])

    context = {
        "next": _safe_next_url(request),
    }

    return render(request, "accounts/login.html", context)


def logout_view(request):
    logout(request)

    login_url = reverse("accounts:login")
    return redirect(f"{login_url}?message=logout")


def auth_start_view(request):
    messages.info(
        request,
        "La autenticación corporativa será habilitada en una etapa posterior.",
    )

    login_url = reverse("accounts:login")
    next_url = _safe_next_url(request)

    if next_url:
        return redirect(f"{login_url}?next={next_url}")

    return redirect(login_url)


def callback_placeholder_view(request):
    messages.warning(
        request,
        "Callback placeholder: esta ruta no autentica usuarios todavía.",
    )

    login_url = reverse("accounts:login")
    return redirect(login_url)