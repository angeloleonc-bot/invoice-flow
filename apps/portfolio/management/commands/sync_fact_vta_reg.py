from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from apps.portfolio.models import (
    Customer,
    CustomerContact,
    CustomerSAPProfile,
    Document,
    DocumentAssignment,
    DocumentStatus,
)


class Command(BaseCommand):
    help = (
        "Sincroniza Fact_Vta_Reg usando comparación en memoria "
        "y escribe solamente cambios reales."
    )

    BATCH_SIZE = 500

    def handle(self, *args, **options):
        rows = self._fetch_rows()

        status, _ = DocumentStatus.objects.get_or_create(
            name="Pendiente",
            defaults={
                "sort_order": 10,
                "is_active": True,
            },
        )

        User = get_user_model()
        now = timezone.now()

        created_customers = 0
        updated_customers = 0
        unchanged_customers = 0

        created_documents = 0
        updated_documents = 0
        unchanged_documents = 0

        created_contacts = 0

        created_assignments = 0
        replaced_assignments = 0
        skipped_assignments = 0
        unchanged_assignments = 0

        # ============================================================
        # 1. CUSTOMER
        #
        # Una empresa puede aparecer en muchas facturas.
        # El comportamiento histórico terminaba dejando los valores
        # de la última fila procesada. Conservamos ese resultado final.
        # ============================================================

        desired_customers = {}

        for row in rows:
            external_id = self._clean(row["Id"])

            desired_customers[external_id] = {
                "rut": self._clean(row["Rut"]),
                "name": self._clean(row["Nombre"]),
                "cluster": self._clean(row["Cluster"]),
                "email": self._first_email(
                    row["E_mail"]
                ),
                "phone": self._clean(
                    row["Telefono"]
                ),
                "is_active": True,
            }

        existing_customers = {
            customer.external_id: customer
            for customer in Customer.objects.filter(
                external_id__in=list(
                    desired_customers.keys()
                )
            )
        }

        sap_profile_customer_ids = set(
            CustomerSAPProfile.objects.values_list(
                "customer_id",
                flat=True,
            )
        )

        customers_to_create = []
        customers_to_update = []

        for external_id, values in desired_customers.items():
            customer = existing_customers.get(
                external_id
            )

            if customer is None:
                customers_to_create.append(
                    Customer(
                        external_id=external_id,
                        rut=values["rut"],
                        name=values["name"],
                        cluster=values["cluster"],
                        email=values["email"],
                        phone=values["phone"],
                        is_active=True,
                    )
                )
                created_customers += 1
                continue

            use_legacy_contact = (
                customer.id
                not in sap_profile_customer_ids
            )

            changed = (
                customer.rut != values["rut"]
                or customer.name != values["name"]
                or customer.cluster != values["cluster"]
                or (
                    use_legacy_contact
                    and customer.email
                    != values["email"]
                )
                or (
                    use_legacy_contact
                    and customer.phone
                    != values["phone"]
                )
                or customer.is_active is not True
            )

            if not changed:
                unchanged_customers += 1
                continue

            customer.rut = values["rut"]
            customer.name = values["name"]
            customer.cluster = values["cluster"]

            if use_legacy_contact:
                customer.email = values["email"]
                customer.phone = values["phone"]

            customer.is_active = True
            customer.updated_at = now

            customers_to_update.append(
                customer
            )
            updated_customers += 1

        with transaction.atomic():
            if customers_to_create:
                Customer.objects.bulk_create(
                    customers_to_create,
                    batch_size=self.BATCH_SIZE,
                )

            if customers_to_update:
                Customer.objects.bulk_update(
                    customers_to_update,
                    fields=[
                        "rut",
                        "name",
                        "cluster",
                        "email",
                        "phone",
                        "is_active",
                        "updated_at",
                    ],
                    batch_size=self.BATCH_SIZE,
                )

        # Recargamos para tener IDs incluso en clientes recién creados.
        customers = {
            customer.external_id: customer
            for customer in Customer.objects.filter(
                external_id__in=list(
                    desired_customers.keys()
                )
            )
        }

        # ============================================================
        # 2. CONTACTOS
        #
        # Preservamos comportamiento anterior:
        # get_or_create solamente creaba faltantes.
        # No actualizaba contactos existentes.
        # ============================================================

        customer_ids = [
            customer.id
            for customer in customers.values()
        ]

        existing_contacts = list(
            CustomerContact.objects.filter(
                customer_id__in=customer_ids
            )
        )

        existing_email_keys = {
            (
                contact.customer_id,
                self._clean(contact.email).lower(),
            )
            for contact in existing_contacts
            if self._clean(contact.email)
        }

        existing_phone_keys = {
            (
                contact.customer_id,
                self._clean(contact.phone),
            )
            for contact in existing_contacts
            if self._clean(contact.phone)
        }

        pending_email_keys = set()
        pending_phone_keys = set()

        contacts_to_create = []

        for row in rows:
            customer = customers[
                self._clean(row["Id"])
            ]

            emails = self._split_emails(
                row["E_mail"]
            )

            do_not_contact = self._is_yes(
                row["No_Enviar"]
            )

            send_frequency = self._send_frequency(
                row
            )

            phone = self._clean(
                row["Telefono"]
            )

            for index, email in enumerate(emails):
                key = (
                    customer.id,
                    email,
                )

                if (
                    key in existing_email_keys
                    or key in pending_email_keys
                ):
                    continue

                contacts_to_create.append(
                    CustomerContact(
                        customer=customer,
                        name=customer.name,
                        email=email,
                        phone=phone,
                        is_primary=(index == 0),
                        do_not_contact=do_not_contact,
                        send_frequency=send_frequency,
                    )
                )

                pending_email_keys.add(key)
                created_contacts += 1

            if phone and not emails:
                key = (
                    customer.id,
                    phone,
                )

                if (
                    key in existing_phone_keys
                    or key in pending_phone_keys
                ):
                    continue

                contacts_to_create.append(
                    CustomerContact(
                        customer=customer,
                        name=customer.name,
                        email="",
                        phone=phone,
                        is_primary=True,
                        do_not_contact=do_not_contact,
                        send_frequency=send_frequency,
                    )
                )

                pending_phone_keys.add(key)
                created_contacts += 1

        if contacts_to_create:
            CustomerContact.objects.bulk_create(
                contacts_to_create,
                batch_size=self.BATCH_SIZE,
            )

        # ============================================================
        # 3. DOCUMENTOS
        # ============================================================

        trans_ids = {
            str(row["TransId"])
            for row in rows
        }

        existing_documents = {
            document.trans_id: document
            for document in (
                Document.objects
                .filter(
                    external_source=(
                        Document.SOURCE_FACT_VTA_REG
                    ),
                    trans_id__in=list(trans_ids),
                )
                .select_related(
                    "customer",
                    "status",
                    "sub_status",
                )
            )
        }

        documents_to_create = []
        documents_to_update = []

        # Fact_Vta_Reg puede contener filas físicamente repetidas.
        # Para documentos trabajamos una sola vez por TransId.
        # Si el mismo TransId aparece varias veces, prevalece la
        # última fila, consistente con la lógica de asignaciones.
        (
            desired_document_rows,
            duplicate_document_rows,
        ) = self._deduplicate_document_rows(rows)

        for trans_id, row in desired_document_rows.items():

            customer = customers[
                self._clean(row["Id"])
            ]

            original_amount = Decimal(
                row["Total_Doc"] or 0
            )

            values = {
                "customer_id": customer.id,
                "document_type": (
                    Document.DOCUMENT_TYPE_INVOICE
                ),
                "document_number": str(
                    row["Num_doc"]
                ),
                "source_doc_entry": self._clean(
                    row["DocEntry"]
                ),
                "source_base_folio": self._clean(
                    row["Folio_Base"]
                ),
                "document_subtype": self._clean(
                    row["DocSubType"]
                ),
                "issue_date": (
                    row["Fecha_Documento"].date()
                ),
                "due_date": (
                    row[
                        "Vencimiento_Documento"
                    ].date()
                ),
                "original_amount": original_amount,
                "payment_terms": self._clean(
                    row["Condicion_Pago"]
                ),
                "seller_name": self._clean(
                    row["Vendedor_Doc"]
                ),
                "market_place": self._clean(
                    row["Market_Place"]
                ),
                "purchase_order": self._clean(
                    row["OC"]
                ),
                "work_reference": self._clean(
                    row["Obra"]
                ),
                "is_claimed": bool(
                    self._clean(
                        row["Reclamada"]
                    )
                ),
                "is_refactored": bool(
                    self._clean(
                        row["Refacturacion"]
                    )
                ),
                "source_snapshot_date": (
                    self._aware_datetime(
                        row["Fecha_Informe"]
                    )
                ),
            }

            document = existing_documents.get(
                trans_id
            )

            if document is None:
                documents_to_create.append(
                    Document(
                        external_source=(
                            Document.SOURCE_FACT_VTA_REG
                        ),
                        trans_id=trans_id,
                        customer=customer,
                        document_type=(
                            values["document_type"]
                        ),
                        document_number=(
                            values["document_number"]
                        ),
                        source_doc_entry=(
                            values["source_doc_entry"]
                        ),
                        source_base_folio=(
                            values["source_base_folio"]
                        ),
                        document_subtype=(
                            values["document_subtype"]
                        ),
                        issue_date=values["issue_date"],
                        due_date=values["due_date"],
                        original_amount=original_amount,

                        # Solo documentos NUEVOS se inicializan
                        # financieramente.
                        balance_amount=original_amount,
                        overpayment_amount=Decimal("0"),
                        status=status,

                        payment_terms=(
                            values["payment_terms"]
                        ),
                        seller_name=(
                            values["seller_name"]
                        ),
                        market_place=(
                            values["market_place"]
                        ),
                        purchase_order=(
                            values["purchase_order"]
                        ),
                        work_reference=(
                            values["work_reference"]
                        ),
                        is_claimed=(
                            values["is_claimed"]
                        ),
                        is_refactored=(
                            values["is_refactored"]
                        ),
                        source_snapshot_date=(
                            values[
                                "source_snapshot_date"
                            ]
                        ),
                    )
                )

                created_documents += 1
                continue

            changed = (
                document.customer_id
                != values["customer_id"]
                or document.document_type
                != values["document_type"]
                or document.document_number
                != values["document_number"]
                or document.source_doc_entry
                != values["source_doc_entry"]
                or document.source_base_folio
                != values["source_base_folio"]
                or document.document_subtype
                != values["document_subtype"]
                or document.issue_date
                != values["issue_date"]
                or document.due_date
                != values["due_date"]
                or document.original_amount
                != values["original_amount"]
                or document.payment_terms
                != values["payment_terms"]
                or document.seller_name
                != values["seller_name"]
                or document.market_place
                != values["market_place"]
                or document.purchase_order
                != values["purchase_order"]
                or document.work_reference
                != values["work_reference"]
                or document.is_claimed
                != values["is_claimed"]
                or document.is_refactored
                != values["is_refactored"]
                or document.source_snapshot_date
                != values["source_snapshot_date"]
            )

            if not changed:
                unchanged_documents += 1
                continue

            document.customer_id = (
                values["customer_id"]
            )
            document.document_type = (
                values["document_type"]
            )
            document.document_number = (
                values["document_number"]
            )
            document.source_doc_entry = (
                values["source_doc_entry"]
            )
            document.source_base_folio = (
                values["source_base_folio"]
            )
            document.document_subtype = (
                values["document_subtype"]
            )
            document.issue_date = (
                values["issue_date"]
            )
            document.due_date = (
                values["due_date"]
            )
            document.original_amount = (
                values["original_amount"]
            )
            document.payment_terms = (
                values["payment_terms"]
            )
            document.seller_name = (
                values["seller_name"]
            )
            document.market_place = (
                values["market_place"]
            )
            document.purchase_order = (
                values["purchase_order"]
            )
            document.work_reference = (
                values["work_reference"]
            )
            document.is_claimed = (
                values["is_claimed"]
            )
            document.is_refactored = (
                values["is_refactored"]
            )
            document.source_snapshot_date = (
                values["source_snapshot_date"]
            )
            document.updated_at = now

            # IMPORTANTE:
            # NO tocar balance/status/substatus en documentos
            # existentes. Son estado financiero derivado.

            documents_to_update.append(
                document
            )

            updated_documents += 1

        with transaction.atomic():
            if documents_to_create:
                Document.objects.bulk_create(
                    documents_to_create,
                    batch_size=self.BATCH_SIZE,
                )

            if documents_to_update:
                Document.objects.bulk_update(
                    documents_to_update,
                    fields=[
                        "customer",
                        "document_type",
                        "document_number",
                        "source_doc_entry",
                        "source_base_folio",
                        "document_subtype",
                        "issue_date",
                        "due_date",
                        "original_amount",
                        "payment_terms",
                        "seller_name",
                        "market_place",
                        "purchase_order",
                        "work_reference",
                        "is_claimed",
                        "is_refactored",
                        "source_snapshot_date",
                        "updated_at",
                    ],
                    batch_size=self.BATCH_SIZE,
                )

        # Recargar documentos, incluyendo los recién creados.
        documents = {
            document.trans_id: document
            for document in (
                Document.objects
                .filter(
                    external_source=(
                        Document.SOURCE_FACT_VTA_REG
                    ),
                    trans_id__in=list(trans_ids),
                )
            )
        }

        # ============================================================
        # 4. ASIGNACIONES
        # ============================================================

        document_ids = [
            document.id
            for document in documents.values()
        ]

        active_assignments = {
            assignment.document_id: assignment
            for assignment in (
                DocumentAssignment.objects
                .filter(
                    document_id__in=document_ids,
                    is_active=True,
                )
                .select_related("assigned_to")
            )
        }

        collector_emails = {
            self._clean(
                row["Cobrador_Email"]
            ).lower()
            for row in rows
            if self._clean(
                row["Cobrador_Email"]
            )
        }

        users = {
            user.email.lower(): user
            for user in User.objects.filter(
                is_active=True
            )
            if user.email
            and user.email.lower()
            in collector_emails
        }

        assignments_to_deactivate = []
        assignments_to_create = []

        # Si una factura apareciera repetida en fuente,
        # procesamos la última fila para mantener estado final.
        desired_assignment_rows = {}

        for row in rows:
            desired_assignment_rows[
                str(row["TransId"])
            ] = row

        for trans_id, row in desired_assignment_rows.items():
            document = documents[
                trans_id
            ]

            email = self._clean(
                row["Cobrador_Email"]
            ).lower()

            if not email:
                skipped_assignments += 1
                continue

            collector = users.get(email)

            if collector is None:
                skipped_assignments += 1
                continue

            current = active_assignments.get(
                document.id
            )

            if (
                current
                and current.assigned_to_id
                == collector.id
            ):
                unchanged_assignments += 1
                continue

            if current:
                current.is_active = False
                current.notes = (
                    f"{current.notes}\n"
                    "Reemplazada por sincronización "
                    "Fact_Vta_Reg."
                ).strip()

                assignments_to_deactivate.append(
                    current
                )

                replaced_assignments += 1

            assignments_to_create.append(
                DocumentAssignment(
                    document=document,
                    assigned_to=collector,
                    assigned_by=collector,
                    assignment_type=(
                        DocumentAssignment
                        .ASSIGNMENT_TYPE_INITIAL
                    ),
                    notes=(
                        "Asignación importada desde "
                        "Fact_Vta_Reg. Cobrador fuente: "
                        f"{self._clean(row['Cobrador'])}"
                    ),
                )
            )

            created_assignments += 1

        with transaction.atomic():
            if assignments_to_deactivate:
                DocumentAssignment.objects.bulk_update(
                    assignments_to_deactivate,
                    fields=[
                        "is_active",
                        "notes",
                    ],
                    batch_size=self.BATCH_SIZE,
                )

            if assignments_to_create:
                DocumentAssignment.objects.bulk_create(
                    assignments_to_create,
                    batch_size=self.BATCH_SIZE,
                )

        # ============================================================
        # OUTPUT
        # ============================================================

        self.stdout.write(
            self.style.SUCCESS(
                "Sincronización Fact_Vta_Reg completada."
            )
        )

        self.stdout.write(
            f"Filas fuente: {len(rows)}"
        )

        self.stdout.write(
            f"Clientes creados: {created_customers}"
        )

        self.stdout.write(
            "Clientes actualizados realmente: "
            f"{updated_customers}"
        )

        self.stdout.write(
            "Clientes sin cambios: "
            f"{unchanged_customers}"
        )

        self.stdout.write(
            f"Documentos creados: {created_documents}"
        )

        self.stdout.write(
            "Filas duplicadas de documentos omitidas por TransId: "
            f"{duplicate_document_rows}"
        )

        self.stdout.write(
            "Documentos actualizados realmente: "
            f"{updated_documents}"
        )

        self.stdout.write(
            "Documentos sin cambios: "
            f"{unchanged_documents}"
        )

        self.stdout.write(
            f"Contactos creados: {created_contacts}"
        )

        self.stdout.write(
            "Asignaciones creadas: "
            f"{created_assignments}"
        )

        self.stdout.write(
            "Asignaciones reemplazadas: "
            f"{replaced_assignments}"
        )

        self.stdout.write(
            "Asignaciones sin cambios: "
            f"{unchanged_assignments}"
        )

        self.stdout.write(
            "Asignaciones omitidas: "
            f"{skipped_assignments}"
        )

    def _fetch_rows(self):
        query = """
            SELECT
                TransId,
                Cobrador,
                Cobrador_Email,
                Id,
                Rut,
                Nombre,
                Cluster,
                Telefono,
                E_mail,
                Tipo,
                Tipo_Doc,
                DocEntry,
                Num_doc,
                Vendedor_Doc,
                Market_Place,
                OC,
                Obra,
                Reclamada,
                Fecha_Documento,
                Vencimiento_Documento,
                Condicion_Pago,
                Refacturacion,
                Folio_Base,
                Total_Doc,
                ISNULL(Saldo_Pagado, 0) AS Saldo_Pagado,
                Envio_Semanal,
                Envio_Quincenal,
                Envio_Mensual,
                No_Enviar,
                DocSubType,
                Fecha_Informe
            FROM [dbo].[Fact_Vta_Reg]
            WHERE
                TransId IS NOT NULL
                AND Id IS NOT NULL
                AND Rut IS NOT NULL
                AND Nombre IS NOT NULL
                AND Num_doc IS NOT NULL
                AND Fecha_Documento IS NOT NULL
                AND Vencimiento_Documento IS NOT NULL
        """

        with connection.cursor() as cursor:
            cursor.execute(query)

            columns = [
                column[0]
                for column in cursor.description
            ]

            return [
                dict(zip(columns, row))
                for row in cursor.fetchall()
            ]

    @staticmethod
    def _deduplicate_document_rows(rows):
        desired_document_rows = {}

        for row in rows:
            desired_document_rows[
                str(row["TransId"])
            ] = row

        duplicate_document_rows = (
            len(rows) - len(desired_document_rows)
        )

        return (
            desired_document_rows,
            duplicate_document_rows,
        )

    def _split_emails(self, value):
        value = self._clean(value)

        if not value:
            return []

        raw_emails = (
            value
            .replace(",", ";")
            .split(";")
        )

        return [
            email.strip().lower()
            for email in raw_emails
            if email.strip()
        ]

    def _first_email(self, value):
        emails = self._split_emails(value)
        return emails[0] if emails else ""

    def _send_frequency(self, row):
        if self._is_yes(row["No_Enviar"]):
            return "none"

        if self._is_yes(row["Envio_Semanal"]):
            return "weekly"

        if self._is_yes(row["Envio_Quincenal"]):
            return "biweekly"

        if self._is_yes(row["Envio_Mensual"]):
            return "monthly"

        return ""

    def _is_yes(self, value):
        return (
            self._clean(value).upper()
            == "Y"
        )

    def _clean(self, value):
        if value is None:
            return ""

        return str(value).strip()

    def _aware_datetime(self, value):
        if value is None:
            return None

        if timezone.is_naive(value):
            return timezone.make_aware(
                value
            )

        return value
