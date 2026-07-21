from django.conf import settings
from django.db import models
from django.db.models import Q


class Customer(models.Model):
    external_id = models.CharField(max_length=100, unique=True)
    rut = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=255)
    cluster = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["rut"]),
            models.Index(fields=["external_id"]),
            models.Index(fields=["name"]),
            models.Index(fields=["cluster"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.rut})"


class CustomerContact(models.Model):
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="contacts",
    )
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    position = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    is_primary = models.BooleanField(default=False)
    do_not_contact = models.BooleanField(default=False)
    send_frequency = models.CharField(max_length=30, blank=True)

    class Meta:
        verbose_name = "Contacto de cliente"
        verbose_name_plural = "Contactos de cliente"
        ordering = ["customer__name", "-is_primary", "name"]
        indexes = [
            models.Index(fields=["customer"]),
            models.Index(fields=["email"]),
            models.Index(fields=["is_primary"]),
        ]

    def __str__(self):
        return f"{self.name} - {self.customer.name}"


class DocumentStatus(models.Model):
    name = models.CharField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Estado de documento"
        verbose_name_plural = "Estados de documento"
        ordering = ["sort_order", "name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return self.name


class DocumentSubStatus(models.Model):
    name = models.CharField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Subestado de documento"
        verbose_name_plural = "Subestados de documento"
        ordering = ["sort_order", "name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return self.name


class DocumentTag(models.Model):
    name = models.CharField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Etiqueta de documento"
        verbose_name_plural = "Etiquetas de documento"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return self.name


class Document(models.Model):
    DOCUMENT_TYPE_INVOICE = "invoice"
    DOCUMENT_TYPE_CREDIT_NOTE = "credit_note"
    DOCUMENT_TYPE_DEBIT_NOTE = "debit_note"
    DOCUMENT_TYPE_OTHER = "other"

    DOCUMENT_TYPE_CHOICES = [
        (DOCUMENT_TYPE_INVOICE, "Factura"),
        (DOCUMENT_TYPE_CREDIT_NOTE, "Nota de crédito"),
        (DOCUMENT_TYPE_DEBIT_NOTE, "Nota de débito"),
        (DOCUMENT_TYPE_OTHER, "Otro"),
    ]

    SOURCE_MANUAL = "manual"
    SOURCE_FACT_VTA_REG = "Fact_Vta_Reg"
    SOURCE_NC_VTA_REG = "NC_Vta_Reg"
    SOURCE_PAGO_VTA_REG = "Pago_Vta_Reg"

    SOURCE_CHOICES = [
        (SOURCE_MANUAL, "Manual"),
        (SOURCE_FACT_VTA_REG, "Fact_Vta_Reg"),
        (SOURCE_NC_VTA_REG, "NC_Vta_Reg"),
        (SOURCE_PAGO_VTA_REG, "Pago_Vta_Reg"),
    ]

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="documents")
    trans_id = models.CharField(max_length=100)
    document_type = models.CharField(max_length=30, choices=DOCUMENT_TYPE_CHOICES, default=DOCUMENT_TYPE_INVOICE)
    document_number = models.CharField(max_length=100)
    source_doc_entry = models.CharField(max_length=100, blank=True)
    source_base_folio = models.CharField(max_length=100, blank=True)
    document_subtype = models.CharField(max_length=30, blank=True)
    issue_date = models.DateField()
    due_date = models.DateField()
    original_amount = models.DecimalField(max_digits=18, decimal_places=2)
    balance_amount = models.DecimalField(max_digits=18, decimal_places=2)
    overpayment_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=0,
    )
    payment_terms = models.CharField(max_length=100, blank=True)
    seller_name = models.CharField(max_length=150, blank=True)
    market_place = models.CharField(max_length=150, blank=True)
    is_claimed = models.BooleanField(default=False)
    is_refactored = models.BooleanField(default=False)
    source_snapshot_date = models.DateTimeField(null=True, blank=True)
    status = models.ForeignKey(DocumentStatus, on_delete=models.PROTECT, related_name="documents")
    sub_status = models.ForeignKey(DocumentSubStatus, on_delete=models.SET_NULL, null=True, blank=True, related_name="documents")
    tags = models.ManyToManyField(DocumentTag, blank=True, related_name="documents")
    external_source = models.CharField(max_length=50, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Documento"
        verbose_name_plural = "Documentos"
        ordering = ["due_date", "customer__name", "document_number"]
        constraints = [
            models.UniqueConstraint(fields=["external_source", "trans_id"], name="unique_document_by_source_trans_id"),
            models.UniqueConstraint(fields=["customer", "document_type", "document_number"], name="unique_document_by_customer_type_number"),
        ]
        indexes = [
            models.Index(fields=["customer"]),
            models.Index(fields=["trans_id"]),
            models.Index(fields=["document_type"]),
            models.Index(fields=["document_number"]),
            models.Index(fields=["issue_date"]),
            models.Index(fields=["due_date"]),
            models.Index(fields=["status"]),
            models.Index(fields=["sub_status"]),
            models.Index(fields=["external_source"]),
            models.Index(fields=["balance_amount"]),
        ]

    def __str__(self):
        return f"{self.get_document_type_display()} {self.document_number} - {self.customer.name}"


class DocumentAssignment(models.Model):
    ASSIGNMENT_TYPE_INITIAL = "initial"
    ASSIGNMENT_TYPE_REASSIGNMENT = "reassignment"
    ASSIGNMENT_TYPE_BALANCING = "balancing"
    ASSIGNMENT_TYPE_MANUAL = "manual"

    ASSIGNMENT_TYPE_CHOICES = [
        (ASSIGNMENT_TYPE_INITIAL, "Asignación inicial"),
        (ASSIGNMENT_TYPE_REASSIGNMENT, "Reasignación"),
        (ASSIGNMENT_TYPE_BALANCING, "Balanceo de carga"),
        (ASSIGNMENT_TYPE_MANUAL, "Manual"),
    ]

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="portfolio_assignments_received",
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="portfolio_assignments_created",
    )
    assignment_type = models.CharField(
        max_length=30,
        choices=ASSIGNMENT_TYPE_CHOICES,
        default=ASSIGNMENT_TYPE_MANUAL,
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "Asignación de documento"
        verbose_name_plural = "Asignaciones de documentos"
        ordering = ["-assigned_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["document"],
                condition=Q(is_active=True),
                name="unique_active_assignment_per_document",
            ),
        ]
        indexes = [
            models.Index(fields=["document"]),
            models.Index(fields=["assigned_to"]),
            models.Index(fields=["assigned_by"]),
            models.Index(fields=["assignment_type"]),
            models.Index(fields=["assigned_at"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return f"{self.document} → {self.assigned_to}"
    
class PaymentRecord(models.Model):
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="payments",
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="payments",
    )
    payment_date = models.DateField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    source_reference = models.CharField(max_length=120, blank=True)
    external_payment_id = models.CharField(max_length=120, unique=True, null=True, blank=True)
    source_table = models.CharField(max_length=120, blank=True, default=Document.SOURCE_PAGO_VTA_REG)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Pago aplicado"
        verbose_name_plural = "Pagos aplicados"
        ordering = ["-payment_date", "-created_at"]
        indexes = [
            models.Index(fields=["document"]),
            models.Index(fields=["customer"]),
            models.Index(fields=["payment_date"]),
            models.Index(fields=["external_payment_id"]),
        ]

    def __str__(self):
        return f"Pago {self.amount} - {self.document}"
    
class CreditNoteApplication(models.Model):
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="credit_note_applications",
    )
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="credit_note_applications",
    )

    credit_trans_id = models.CharField(max_length=100)
    credit_document_number = models.CharField(max_length=100)
    source_base_folio = models.CharField(max_length=100, blank=True)
    credit_doc_entry = models.CharField(max_length=100, blank=True)

    target_invoice_number = models.CharField(max_length=100)
    reference_type = models.CharField(max_length=50, blank=True)
    credit_type = models.CharField(max_length=50, blank=True)

    credit_amount = models.DecimalField(max_digits=18, decimal_places=2)
    invoice_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    status = models.CharField(max_length=30, blank=True)
    reason = models.CharField(max_length=150, blank=True)
    comment = models.TextField(blank=True)

    issue_date = models.DateField(null=True, blank=True)
    source_snapshot_date = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Aplicación de nota de crédito"
        verbose_name_plural = "Aplicaciones de notas de crédito"
        ordering = ["-issue_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["credit_trans_id", "document"],
                name="unique_credit_note_application_by_trans_id_document",
            ),
        ]
        indexes = [
            models.Index(fields=["customer"]),
            models.Index(fields=["document"]),
            models.Index(fields=["credit_trans_id"]),
            models.Index(fields=["credit_document_number"]),
            models.Index(fields=["target_invoice_number"]),
            models.Index(fields=["status"]),
            models.Index(fields=["issue_date"]),
        ]

    def __str__(self):
        return f"NC {self.credit_document_number} → Factura {self.target_invoice_number}"
    
class DocumentSupport(models.Model):
    id = models.BigIntegerField(
        primary_key=True,
        db_column="Id_Pk",
    )
    document_number = models.IntegerField(
        null=True,
        blank=True,
        db_column="doc_num",
    )
    url = models.TextField(
        null=True,
        blank=True,
        db_column="url",
    )
    onedrive_item_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_column="onedrive_item_id",
    )
    is_deleted = models.BooleanField(
        default=False,
        db_column="eliminado",
    )

    class Meta:
        managed = False
        db_table = "Fact_scan_url2"
        ordering = ["id"]
        verbose_name = "Respaldo documental"
        verbose_name_plural = "Respaldos documentales"

    def __str__(self):
        return f"Respaldo {self.document_number} ({self.id})"