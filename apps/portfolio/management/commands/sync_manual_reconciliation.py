from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from apps.portfolio.models import (
    Document,
    ManualReconciliationApplication,
)
from apps.portfolio.services.financial import (
    recalculate_document_financial_state,
)


class Command(BaseCommand):
    help = (
        "Sincroniza reconciliaciones manuales desde "
        "Pago_Reconciliacion_Manual y recalcula saldos."
    )

    SOURCE_TABLE = "Pago_Reconciliacion_Manual"

    def handle(self, *args, **options):
        rows = self._fetch_rows()

        created = 0
        updated = 0
        invalid_match = 0
        skipped_zero = 0

        affected_document_ids = set()

        with transaction.atomic():
            for row in rows:
                document = self._find_document(row)

                if not document:
                    invalid_match += 1
                    continue

                amount = self._decimal(
                    row["Total_Recon_Manual"]
                )

                if amount == Decimal("0"):
                    skipped_zero += 1
                    continue

                obj, was_created = (
                    ManualReconciliationApplication
                    .objects
                    .update_or_create(
                        source_id=row["Id_PK"],
                        defaults={
                            "document": document,
                            "customer": document.customer,
                            "invoice_number": str(
                                row["FolioNum"]
                            ).strip(),
                            "source_rut": self._clean(
                                row["Rut"]
                            ),
                            "amount": amount,
                            "timeline_order_at": (
                                self._aware_datetime(
                                    row["Fecha_Carga"]
                                )
                            ),
                            "source_table": self.SOURCE_TABLE,
                        },
                    )
                )

                if was_created:
                    created += 1
                else:
                    updated += 1

                affected_document_ids.add(document.id)

            documents_with_financial_effect = 0
            documents_without_financial_effect = 0

            for document_id in affected_document_ids:
                financial_result = (
                    recalculate_document_financial_state(
                        document_id
                    )
                )

                if (
                    financial_result[
                        "manual_reconciliation_applied"
                    ]
                    > Decimal("0")
                ):
                    documents_with_financial_effect += 1
                else:
                    documents_without_financial_effect += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Sincronización de reconciliaciones "
                "manuales completada."
            )
        )
        self.stdout.write(
            f"Filas fuente: {len(rows)}"
        )
        self.stdout.write(
            f"Reconciliaciones creadas: {created}"
        )
        self.stdout.write(
            f"Reconciliaciones actualizadas: {updated}"
        )
        self.stdout.write(
            f"Sin factura válida: {invalid_match}"
        )
        self.stdout.write(
            f"Filas monto cero: {skipped_zero}"
        )
        self.stdout.write(
            "Reconciliaciones válidas almacenadas: "
            f"{created + updated}"
        )
        self.stdout.write(
            "Facturas con efecto financiero neto: "
            f"{documents_with_financial_effect}"
        )
        self.stdout.write(
            "Facturas sin efecto financiero neto: "
            f"{documents_without_financial_effect}"
        )
        self.stdout.write(
            "Facturas recalculadas: "
            f"{len(affected_document_ids)}"
        )

    def _fetch_rows(self):
        query = """
            SELECT
                Id_PK,
                FolioNum,
                Total_Recon_Manual,
                Rut,
                Fecha_Carga
            FROM [dbo].[Pago_Reconciliacion_Manual]
            WHERE
                Id_PK IS NOT NULL
                AND FolioNum IS NOT NULL
                AND Total_Recon_Manual IS NOT NULL
                AND Rut IS NOT NULL
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

    def _find_document(self, row):
        folio = str(row["FolioNum"]).strip()
        source_rut_body = self._rut_body(
            row["Rut"]
        )

        if not source_rut_body:
            return None

        candidates = (
            Document.objects
            .select_related("customer")
            .filter(
                external_source=(
                    Document.SOURCE_FACT_VTA_REG
                ),
                document_type=(
                    Document.DOCUMENT_TYPE_INVOICE
                ),
                document_number=folio,
            )
            .order_by("id")
        )

        matches = [
            document
            for document in candidates
            if self._rut_body(document.customer.rut)
            == source_rut_body
        ]

        # La reconciliación solo se acepta
        # cuando existe exactamente una factura válida.
        if len(matches) != 1:
            return None

        return matches[0]

    def _rut_body(self, value):
        value = self._clean(value)

        if not value:
            return ""

        if "-" in value:
            return value.split("-", 1)[0].strip()

        return value

    def _decimal(self, value):
        if value is None:
            return Decimal("0")

        return Decimal(str(value))

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