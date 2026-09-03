from django.conf import settings
from django.db import models
from django.utils import timezone
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.contrib.contenttypes.fields import GenericRelation



class CollectionAction(models.Model):
    class ActionType(models.TextChoices):
        CALL = "CALL", "Llamada"
        EMAIL = "EMAIL", "Email"
        WHATSAPP = "WHATSAPP", "WhatsApp"
        NOTE = "NOTE", "Nota"
        PROMISE = "PROMISE", "Promesa"
        PAYMENT_INFO = "PAYMENT_INFO", "Información de pago"
        ALERT_RESOLVED = "ALERT_RESOLVED", "Alerta resuelta"
        ALERT_POSTPONED = "ALERT_POSTPONED", "Alerta pospuesta"
        ALERT_REOPENED = "ALERT_REOPENED", "Alerta reabierta"

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

    attachments = GenericRelation(
        "management.OperationalAttachment",
        content_type_field="content_type",
        object_id_field="object_id",
        related_query_name="collection_action",
    )

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

    attachments = GenericRelation(
        "management.OperationalAttachment",
        content_type_field="content_type",
        object_id_field="object_id",
        related_query_name="payment_promise",
    )

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
        null=True,
        blank=True,
    )

    # Identidad persistente del documento externo mientras todavía
    # no existe dentro del dominio operacional Document.
    source = models.CharField(
        max_length=50,
        blank=True,
    )
    source_customer_external_id = models.CharField(
        max_length=100,
        blank=True,
    )
    source_trans_id = models.CharField(
        max_length=100,
        blank=True,
    )
    source_doc_entry = models.CharField(
        max_length=100,
        blank=True,
    )
    source_document_number = models.CharField(
        max_length=100,
        blank=True,
    )

    # Snapshot histórico de lo observado al crear la promesa.
    # Estos valores no representan el saldo financiero vigente.
    source_due_date = models.DateField(
        null=True,
        blank=True,
    )
    source_original_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
    )
    source_balance_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Documento asociado a promesa"
        verbose_name_plural = "Documentos asociados a promesas"

        constraints = [
            models.UniqueConstraint(
                fields=["promise", "document"],
                condition=models.Q(document__isnull=False),
                name="unique_promise_document",
            ),
        ]

        indexes = [
            models.Index(fields=["promise"]),
            models.Index(fields=["document"]),
            models.Index(
                fields=["source", "source_trans_id"],
                name="promise_source_trans_idx",
            ),
            models.Index(
                fields=[
                    "source",
                    "source_customer_external_id",
                    "source_trans_id",
                ],
                name="promise_ext_identity_idx",
            ),
        ]

    def clean(self):
        super().clean()

        if self.document_id is not None:
            return

        required_external_identity = {
            "source": self.source,
            "source_customer_external_id": (
                self.source_customer_external_id
            ),
            "source_trans_id": self.source_trans_id,
            "source_doc_entry": self.source_doc_entry,
            "source_document_number": (
                self.source_document_number
            ),
        }

        missing_fields = [
            field_name
            for field_name, value in required_external_identity.items()
            if not str(value or "").strip()
        ]

        if missing_fields:
            raise ValidationError(
                "Una relación de promesa sin Document debe "
                "conservar identidad ERP externa completa."
            )

        duplicated = (
            PromiseDocument.objects
            .filter(
                promise=self.promise,
                document__isnull=True,
                source=self.source,
                source_customer_external_id=(
                    self.source_customer_external_id
                ),
                source_trans_id=self.source_trans_id,
                source_doc_entry=self.source_doc_entry,
            )
        )

        if self.pk:
            duplicated = duplicated.exclude(pk=self.pk)

        if duplicated.exists():
            raise ValidationError(
                "Este documento externo ya está asociado "
                "a la misma promesa."
            )

    def __str__(self):
        if self.document_id is not None:
            target = str(self.document)
        elif self.source_document_number:
            target = (
                f"{self.source} "
                f"{self.source_document_number}"
            )
        else:
            target = "Documento externo"

        return f"{self.promise} → {target}"


class OperationalAttachment(models.Model):
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="operational_attachments",
    )
    object_id = models.PositiveBigIntegerField()

    attached_to = GenericForeignKey(
        "content_type",
        "object_id",
    )

    original_filename = models.CharField(max_length=255)
    storage_key = models.CharField(max_length=700, unique=True)

    mime_type = models.CharField(max_length=150)
    extension = models.CharField(max_length=20)
    size_bytes = models.PositiveBigIntegerField()

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operational_attachments_uploaded",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Adjunto operacional"
        verbose_name_plural = "Adjuntos operacionales"
        indexes = [
            models.Index(
                fields=["content_type", "object_id"],
                name="mgmt_attach_target_idx",
            ),
            models.Index(
                fields=["created_at"],
                name="mgmt_attach_created_idx",
            ),
            models.Index(
                fields=["uploaded_by"],
                name="mgmt_attach_user_idx",
            ),
        ]

    def clean(self):
        super().clean()

        allowed_models = {
            "collectionaction",
            "paymentpromise",
        }

        if (
            self.content_type_id
            and self.content_type.model not in allowed_models
        ):
            raise ValidationError(
                {
                    "content_type": (
                        "Los adjuntos solo pueden asociarse a una gestión "
                        "o a una promesa de pago."
                    )
                }
            )

    def __str__(self):
        return self.original_filename
    
class PriorityRule(models.Model):
    code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=150)
    score = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    evaluation_order = models.PositiveIntegerField(default=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["evaluation_order", "code"]
        verbose_name = "Regla de prioridad"
        verbose_name_plural = "Reglas de prioridad"

    def __str__(self):
        return f"{self.code} - {self.name}"
    
class OperationalAlert(models.Model):
    class AlertType(models.TextChoices):
        PROMISE_EXPIRED = "PROMISE_EXPIRED", "Promesa vencida"
        PROMISE_DUE_TODAY = "PROMISE_DUE_TODAY", "Promesa vence hoy"
        NO_MANAGEMENT_7_DAYS = "NO_MANAGEMENT_7_DAYS", "Sin gestión 7 días"
        HIGH_PRIORITY_DOCUMENT = "HIGH_PRIORITY_DOCUMENT", "Documento alta prioridad"
        UNASSIGNED_DOCUMENT = "UNASSIGNED_DOCUMENT", "Documento sin asignar"
        CRITICAL_CUSTOMER = "CRITICAL_CUSTOMER", "Cliente crítico"
        PAYMENT_RECEIVED = "PAYMENT_RECEIVED", "Pago recibido"
        PROMISE_FULFILLED = "PROMISE_FULFILLED", "Promesa cumplida"

    class Severity(models.TextChoices):
        CRITICAL = "CRITICAL", "Crítica"
        HIGH = "HIGH", "Alta"
        MEDIUM = "MEDIUM", "Media"
        LOW = "LOW", "Baja"
        INFO = "INFO", "Informativa"

    class AlertStatus(models.TextChoices):
        NEW = "NEW", "Nueva"
        VIEWED = "VIEWED", "Vista"
        IN_PROGRESS = "IN_PROGRESS", "En gestión"
        RESOLVED = "RESOLVED", "Resuelta"
        POSTPONED = "POSTPONED", "Pospuesta"
        DISMISSED = "DISMISSED", "Descartada"
        REOPENED = "REOPENED", "Reabierta"

    alert_type = models.CharField(max_length=40, choices=AlertType.choices)
    severity = models.CharField(max_length=20, choices=Severity.choices)
    title = models.CharField(max_length=180)
    message = models.TextField()

    customer = models.ForeignKey(
        "portfolio.Customer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="operational_alerts",
    )
    document = models.ForeignKey(
        "portfolio.Document",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="operational_alerts",
    )
    promise = models.ForeignKey(
        "management.PaymentPromise",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="operational_alerts",
    )

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="operational_alerts",
    )

    status = models.CharField(
        max_length=20,
        choices=AlertStatus.choices,
        default=AlertStatus.NEW,
    )

    created_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
        models.Index(fields=["alert_type", "status"]),
        models.Index(fields=["severity", "status"]),
        models.Index(fields=["status", "-created_at"]),
        models.Index(fields=["status", "due_at"]),
        models.Index(fields=["assigned_to", "status", "-created_at"]),
        models.Index(fields=["document", "status"]),
        models.Index(fields=["promise", "status"]),
        models.Index(fields=["created_at"]),
        models.Index(fields=["due_at"]),
    ]

    def __str__(self):
        return f"{self.get_severity_display()} - {self.title}"
    
