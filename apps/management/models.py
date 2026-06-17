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


class PaymentPromise(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        ACTIVE = "ACTIVE", "Vigente"
        EXPIRED = "EXPIRED", "Vencida"
        FULFILLED = "FULFILLED", "Cumplida"
        CANCELLED = "CANCELLED", "Cancelada"

    customer = models.ForeignKey(
        "portfolio.Customer",
        on_delete=models.PROTECT,
        related_name="payment_promises",
    )
    promise_date = models.DateField()
    promised_amount = models.DecimalField(max_digits=18, decimal_places=2)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    payment_confirmed = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_promises_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["promise_date", "-created_at"]
        verbose_name = "Promesa de pago"
        verbose_name_plural = "Promesas de pago"
        indexes = [
            models.Index(fields=["customer"]),
            models.Index(fields=["promise_date"]),
            models.Index(fields=["status"]),
            models.Index(fields=["payment_confirmed"]),
            models.Index(fields=["created_by"]),
        ]

    def __str__(self):
        return f"Promesa {self.customer} - {self.promise_date} - {self.promised_amount}"


class PromiseDocument(models.Model):
    promise = models.ForeignKey(
        PaymentPromise,
        on_delete=models.CASCADE,
        related_name="promise_documents",
    )
    document = models.ForeignKey(
        "portfolio.Document",
        on_delete=models.CASCADE,
        related_name="promise_documents",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Documento asociado a promesa"
        verbose_name_plural = "Documentos asociados a promesas"
        constraints = [
            models.UniqueConstraint(
                fields=["promise", "document"],
                name="unique_promise_document",
            ),
        ]
        indexes = [
            models.Index(fields=["promise"]),
            models.Index(fields=["document"]),
        ]

    def __str__(self):
        return f"{self.promise} → {self.document}"