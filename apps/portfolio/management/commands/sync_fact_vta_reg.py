from decimal import Decimal
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from apps.portfolio.models import (
    Customer,
    CustomerContact,
    Document,
    DocumentAssignment,
    DocumentStatus,
)


class Command(BaseCommand):
    help = "Sincroniza facturas desde Fact_Vta_Reg hacia Invoice Flow."

    def handle(self, *args, **options):
        status, _ = DocumentStatus.objects.get_or_create(
            name="Pendiente",
            defaults={"sort_order": 10, "is_active": True},
        )

        User = get_user_model()

        created_customers = 0
        updated_customers = 0
        created_documents = 0
        updated_documents = 0
        created_contacts = 0
        created_assignments = 0
        skipped_assignments = 0

        rows = self._fetch_rows()

        with transaction.atomic():
            for row in rows:
                customer, customer_created = Customer.objects.update_or_create(
                    external_id=self._clean(row["Id"]),
                    defaults={
                        "rut": self._clean(row["Rut"]),
                        "name": self._clean(row["Nombre"]),
                        "cluster": self._clean(row["Cluster"]),
                        "email": self._first_email(row["E_mail"]),
                        "phone": self._clean(row["Telefono"]),
                        "is_active": True,
                    },
                )

                if customer_created:
                    created_customers += 1
                else:
                    updated_customers += 1

                created_contacts += self._sync_contacts(
                    customer=customer,
                    email_value=row["E_mail"],
                    phone_value=row["Telefono"],
                    do_not_contact=self._is_yes(row["No_Enviar"]),
                    send_frequency=self._send_frequency(row),
                )

                document, document_created = Document.objects.update_or_create(
                    external_source=Document.SOURCE_FACT_VTA_REG,
                    trans_id=str(row["TransId"]),
                    defaults={
                        "customer": customer,
                        "document_type": Document.DOCUMENT_TYPE_INVOICE,
                        "document_number": str(row["Num_doc"]),
                        "source_doc_entry": self._clean(row["DocEntry"]),
                        "source_base_folio": self._clean(row["Folio_Base"]),
                        "document_subtype": self._clean(row["DocSubType"]),
                        "issue_date": row["Fecha_Documento"].date(),
                        "due_date": row["Vencimiento_Documento"].date(),
                        "original_amount": Decimal(row["Total_Doc"] or 0),
                        "balance_amount": Decimal(row["Total_Doc"] or 0),
                        "status": status,
                        "payment_terms": self._clean(row["Condicion_Pago"]),
                        "seller_name": self._clean(row["Vendedor_Doc"]),
                        "market_place": self._clean(row["Market_Place"]),
                        "is_claimed": bool(self._clean(row["Reclamada"])),
                        "is_refactored": bool(self._clean(row["Refacturacion"])),
                        "source_snapshot_date": self._aware_datetime(row["Fecha_Informe"]),
                    },
                )

                if document_created:
                    created_documents += 1
                else:
                    updated_documents += 1

                assignment_created = self._sync_assignment(
                    document=document,
                    collector_email=row["Cobrador_Email"],
                    collector_name=row["Cobrador"],
                    User=User,
                )

                if assignment_created is True:
                    created_assignments += 1
                elif assignment_created is False:
                    skipped_assignments += 1

        self.stdout.write(self.style.SUCCESS("Sincronización Fact_Vta_Reg completada."))
        self.stdout.write(f"Filas fuente: {len(rows)}")
        self.stdout.write(f"Clientes creados: {created_customers}")
        self.stdout.write(f"Clientes actualizados: {updated_customers}")
        self.stdout.write(f"Documentos creados: {created_documents}")
        self.stdout.write(f"Documentos actualizados: {updated_documents}")
        self.stdout.write(f"Contactos creados: {created_contacts}")
        self.stdout.write(f"Asignaciones creadas: {created_assignments}")
        self.stdout.write(f"Asignaciones omitidas: {skipped_assignments}")

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
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _sync_contacts(self, customer, email_value, phone_value, do_not_contact, send_frequency):
        created = 0

        emails = self._split_emails(email_value)

        for index, email in enumerate(emails):
            _, was_created = CustomerContact.objects.get_or_create(
                customer=customer,
                email=email,
                defaults={
                    "name": customer.name,
                    "phone": self._clean(phone_value),
                    "is_primary": index == 0,
                    "do_not_contact": do_not_contact,
                    "send_frequency": send_frequency,
                },
            )

            if was_created:
                created += 1

        phone = self._clean(phone_value)

        if phone and not emails:
            _, was_created = CustomerContact.objects.get_or_create(
                customer=customer,
                phone=phone,
                defaults={
                    "name": customer.name,
                    "email": "",
                    "is_primary": True,
                    "do_not_contact": do_not_contact,
                    "send_frequency": send_frequency,
                },
            )

            if was_created:
                created += 1

        return created

    def _sync_assignment(self, document, collector_email, collector_name, User):
        email = self._clean(collector_email)

        if not email:
            return False

        collector = User.objects.filter(email__iexact=email, is_active=True).first()

        if not collector:
            return False

        current_assignment = (
            DocumentAssignment.objects
            .filter(document=document, is_active=True)
            .select_related("assigned_to")
            .first()
        )

        if current_assignment and current_assignment.assigned_to_id == collector.id:
            return None

        if current_assignment:
            current_assignment.is_active = False
            current_assignment.notes = (
                f"{current_assignment.notes}\nReemplazada por sincronización Fact_Vta_Reg."
            ).strip()
            current_assignment.save(update_fields=["is_active", "notes"])

        DocumentAssignment.objects.create(
            document=document,
            assigned_to=collector,
            assigned_by=collector,
            assignment_type=DocumentAssignment.ASSIGNMENT_TYPE_INITIAL,
            notes=f"Asignación importada desde Fact_Vta_Reg. Cobrador fuente: {self._clean(collector_name)}",
        )

        return True

    def _split_emails(self, value):
        value = self._clean(value)

        if not value:
            return []

        raw_emails = value.replace(",", ";").split(";")

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
        return self._clean(value).upper() == "Y"

    def _clean(self, value):
        if value is None:
            return ""

        return str(value).strip()
    
    def _aware_datetime(self, value):
        if value is None:
            return None

        if timezone.is_naive(value):
            return timezone.make_aware(value)

        return value