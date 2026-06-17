from django.conf import settings
from django.db import models
from django.utils import timezone


class CollectionAction(models.Model):
    class ActionType(models.TextChoices):
        CALL = "CALL", "Llamada"
        EMAIL = "EMAIL", "Email"
        WHATSAPP = "WHATSAPP", "WhatsApp"
        NOTE = "NOTE", "Nota"
        PROMISE = "PROMISE", "Promesa"
        PAYMENT_INFO = "PAYMENT_INFO", "Información de pago"

    document = models.ForeignKey(
        "portfolio.Document",
        on_delete=models.CASCADE,
        related_name="collection_actions",
    )
    customer = models.ForeignKey(
        "portfolio.Customer",
        on_delete=models.CASCADE,
        related_name="collection_actions",
    )
    action_type = models.CharField(
        max_length=30,
        choices=ActionType.choices,
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="collection_actions",
    )
    action_date = models.DateTimeField(default=timezone.now)
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-action_date", "-created_at"]
        verbose_name = "Acción de cobranza"
        verbose_name_plural = "Acciones de cobranza"

    def __str__(self):
        return f"{self.get_action_type_display()} - {self.document}"