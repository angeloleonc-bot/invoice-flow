from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    IDENTITY_ERROR = "IDENTITY_ERROR"
    USER_DISABLED = "USER_DISABLED"
    ACCESS_DENIED = "ACCESS_DENIED"

    EVENT_CHOICES = [
        (LOGIN_SUCCESS, "Login exitoso"),
        (LOGIN_FAILED, "Login fallido"),
        (LOGOUT, "Logout"),
        (SESSION_EXPIRED, "Sesión expirada"),
        (TOKEN_EXPIRED, "Token expirado"),
        (IDENTITY_ERROR, "Error de identidad"),
        (USER_DISABLED, "Usuario deshabilitado"),
        (ACCESS_DENIED, "Acceso denegado"),
    ]

    event_type = models.CharField(
        max_length=50,
        choices=EVENT_CHOICES,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    username_snapshot = models.CharField(
        max_length=150,
        blank=True,
        help_text="Usuario informado al momento del evento.",
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Registro de auditoría"
        verbose_name_plural = "Registros de auditoría"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.event_type} - {self.created_at:%Y-%m-%d %H:%M:%S}"