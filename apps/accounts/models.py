from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Usuario local del sistema Invoice Flow.

    En una etapa futura se vinculará con Azure AD / Entra ID.
    La sesión seguirá siendo local Django.
    """

    external_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        unique=True,
        help_text="Identificador externo futuro del proveedor de identidad.",
    )
    identity_provider = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Proveedor externo futuro. Ejemplo: azure_ad.",
    )
    is_identity_active = models.BooleanField(
        default=True,
        help_text="Indica si la identidad externa se considera activa.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Usuario"
        verbose_name_plural = "Usuarios"

    def __str__(self):
        return self.username


class Role(models.Model):
    ADMINISTRADOR = "ADMINISTRADOR"
    SUPERVISOR = "SUPERVISOR"
    COBRADOR = "COBRADOR"
    CONSULTA_AUDITORIA = "CONSULTA_AUDITORIA"

    ROLE_CHOICES = [
        (ADMINISTRADOR, "Administrador"),
        (SUPERVISOR, "Supervisor"),
        (COBRADOR, "Cobrador"),
        (CONSULTA_AUDITORIA, "Consulta / Auditoría"),
    ]

    code = models.CharField(
        max_length=50,
        choices=ROLE_CHOICES,
        unique=True,
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    users = models.ManyToManyField(
        User,
        related_name="roles",
        blank=True,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Rol"
        verbose_name_plural = "Roles"

    def __str__(self):
        return self.name