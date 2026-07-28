from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from apps.audit.services.audit_service import AuditService

import logging
import time

from apps.accounts.contracts import (
    IdentityProviderError,
    IdentityValidationError,
)
from apps.accounts.services.identity_revalidation_service import (
    IdentityRevalidationService,
)

logger = logging.getLogger(__name__)

class CorporateSessionMiddleware:
    """
    Middleware corporativo de sesión local.

    Controla:
    - Acceso a rutas protegidas sin autenticación.
    - Expiración por inactividad.
    - Expiración absoluta.
    - Usuario autenticado pero deshabilitado.
    - Redirección segura al login conservando next cuando corresponde.
    """

    SESSION_KEY_LOGIN_AT = "login_at"
    SESSION_KEY_LAST_ACTIVITY_AT = "last_activity_at"
    SESSION_KEY_NEXT_URL = "next_url"

    PUBLIC_PATH_PREFIXES = (
        "/accounts/login/",
        "/accounts/logout/",
        "/accounts/dev-login/",
        "/accounts/auth/entra/",
        "/accounts/auth/callback/",
        "/admin/login/",
        "/static/",
        "/media/",
        "/favicon.ico",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            if self._is_public_path(request.path):
                return self.get_response(request)

            user = getattr(request, "user", None)

            if not user or not user.is_authenticated:
                return self._redirect_to_login(
                    request,
                    preserve_next=True,
                )

            session_response = self._validate_corporate_session(
                request
            )

            if session_response is not None:
                return session_response

            if not user.is_active:
                self._safe_audit(
                    request=request,
                    user=user,
                    event_type="USER_DISABLED",
                    description="Usuario autenticado deshabilitado. Se fuerza logout local.",
                )
                logout(request)
                return self._redirect_to_login(
                    request=request,
                    message="Tu acceso fue revocado.",
                    preserve_next=False,
                )

            now = timezone.now()
            login_at = self._parse_session_datetime(
                request.session.get(self.SESSION_KEY_LOGIN_AT)
            )
            last_activity_at = self._parse_session_datetime(
                request.session.get(self.SESSION_KEY_LAST_ACTIVITY_AT)
            )

            if login_at is None:
                login_at = now
                request.session[self.SESSION_KEY_LOGIN_AT] = login_at.isoformat()

            if last_activity_at is None:
                last_activity_at = now
                request.session[self.SESSION_KEY_LAST_ACTIVITY_AT] = last_activity_at.isoformat()

            inactivity_timeout = timedelta(
                minutes=getattr(settings, "INACTIVITY_TIMEOUT_MINUTES", 30)
            )
            absolute_timeout = timedelta(
                hours=getattr(settings, "ABSOLUTE_SESSION_TIMEOUT_HOURS", 10)
            )

            expired_by_inactivity = now - last_activity_at > inactivity_timeout
            expired_by_absolute_timeout = now - login_at > absolute_timeout

            if expired_by_inactivity or expired_by_absolute_timeout:
                self._safe_audit(
                    request=request,
                    user=user,
                    event_type="SESSION_EXPIRED",
                    description="Sesión expirada por política de seguridad local.",
                )
                logout(request)
                return self._redirect_to_login(
                    request=request,
                    message="Tu sesión expiró por seguridad.",
                    preserve_next=True,
                )

            request.session[self.SESSION_KEY_LAST_ACTIVITY_AT] = now.isoformat()

            return self.get_response(request)

        except Exception:
            try:
                logout(request)
            except Exception:
                pass

            return self._redirect_to_login(
                request=request,
                message="Tu sesión expiró por seguridad.",
                preserve_next=True,
            )

    def _is_public_path(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self.PUBLIC_PATH_PREFIXES)

    def _redirect_to_login(self, request, message: str, preserve_next: bool):
        login_url = reverse("accounts:login")

        if message:
            messages.warning(request, message)

        if not preserve_next:
            return redirect(login_url)

        next_url = request.get_full_path()

        if not url_has_allowed_host_and_scheme(
            url=next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(login_url)

        request.session[self.SESSION_KEY_NEXT_URL] = next_url
        return redirect(f"{login_url}?next={next_url}")

    def _parse_session_datetime(self, value):
        if not value:
            return None

        if hasattr(value, "tzinfo"):
            return value

        try:
            parsed = timezone.datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None

        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())

        return parsed

    def _safe_audit(self, request, user, event_type: str, description: str):
        try:
            AuditService.log_event(
                event_type=event_type,
                user=user,
                request=request,
                description=description,
            )
        except Exception:
            pass

    def _validate_corporate_session(self, request):
        now = int(time.time())

        session_started_at = int(
            request.session.get(
                "identity_session_started_at",
                now,
            )
        )

        absolute_timeout_seconds = (
            int(
                settings
                .ABSOLUTE_SESSION_TIMEOUT_HOURS
            )
            * 60
            * 60
        )

        if (
            now - session_started_at
            >= absolute_timeout_seconds
        ):
            return self._terminate_session(
                request,
                reason="absolute_timeout",
            )

        provider = str(
            request.session.get(
                "identity_provider",
                "",
            )
        ).strip()

        if provider != "microsoft_entra_id":
            return None

        last_validated_at = int(
            request.session.get(
                "identity_last_validated_at",
                0,
            )
        )

        revalidation_seconds = (
            int(
                settings
                .IDENTITY_REVALIDATION_MINUTES
            )
            * 60
        )

        if (
            last_validated_at
            and now - last_validated_at
            < revalidation_seconds
        ):
            return None

        try:
            IdentityRevalidationService.revalidate(
                user=request.user,
            )

        except IdentityValidationError:
            logger.warning(
                "Sesión terminada por pérdida de autorización.",
                extra={
                    "user_id": request.user.pk,
                    "reason": "identity_access_denied",
                },
            )

            return self._terminate_session(
                request,
                reason="identity_access_denied",
            )

        except IdentityProviderError:
            return self._handle_provider_failure(
                request=request,
                now=now,
                last_validated_at=last_validated_at,
            )

        except Exception:
            logger.exception(
                "Error inesperado durante revalidación de identidad.",
                extra={
                    "user_id": request.user.pk,
                },
            )

            return self._handle_provider_failure(
                request=request,
                now=now,
                last_validated_at=last_validated_at,
            )

        request.session[
            "identity_last_validated_at"
        ] = now

        request.session[
            "identity_revalidation_failures"
        ] = 0

        request.session.modified = True

        return None


    def _handle_provider_failure(
        self,
        *,
        request,
        now: int,
        last_validated_at: int,
    ):
        grace_seconds = (
            int(
                settings
                .IDENTITY_REVALIDATION_GRACE_MINUTES
            )
            * 60
        )

        failures = int(
            request.session.get(
                "identity_revalidation_failures",
                0,
            )
        ) + 1

        request.session[
            "identity_revalidation_failures"
        ] = failures

        request.session.modified = True

        validation_age = (
            now - last_validated_at
            if last_validated_at
            else grace_seconds + 1
        )

        logger.warning(
            "No fue posible revalidar la identidad con Entra.",
            extra={
                "user_id": request.user.pk,
                "failure_count": failures,
                "validation_age": validation_age,
            },
        )

        if validation_age > grace_seconds:
            return self._terminate_session(
                request,
                reason="identity_provider_unavailable",
            )

        return None


    def _terminate_session(
        self,
        request,
        *,
        reason: str,
    ):
        logout(request)

        login_url = reverse(
            "accounts:login"
        )

        return redirect(
            f"{login_url}?reason={reason}"
        )