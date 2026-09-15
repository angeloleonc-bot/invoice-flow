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
        "Pago_Reconciliacion_Manual y recalcula solo "
        "documentos realmente afectados."
    )

    SOURCE_TABLE = "Pago_Reconciliacion_Manual"
    BATCH_SIZE = 500

    def handle(self, *args, **options):
        rows = self._fetch_rows()

        created = 0
        updated = 0
        unchanged = 0
        deleted = 0
        invalid_match = 0
        skipped_zero = 0

        affected_document_ids = set()

        # ------------------------------------------------------------
        # Documentos candidatos agrupados por folio.
        # ------------------------------------------------------------

        documents = list(
            Document.objects
            .filter(
                external_source=(
                    Document.SOURCE_FACT_VTA_REG
                ),
                document_type=(
                    Document.DOCUMENT_TYPE_INVOICE
                ),
            )
            .select_related("customer")
            .order_by("id")
        )

        documents_by_folio = {}

        for document in documents:
            folio = str(
                document.document_number
            ).strip()

            documents_by_folio.setdefault(
                folio,
                [],
            ).append(document)

        # ------------------------------------------------------------
        # Snapshot local completo.
        # ------------------------------------------------------------

        existing_by_source_id = {
            str(obj.source_id): obj
            for obj in (
                ManualReconciliationApplication.objects
                .select_related(
                    "document",
                    "customer",
                )
                .all()
            )
        }

        source_ids = set()

        to_create = []
        to_update = []

        for row in rows:
            source_id = str(
                row["Id_PK"]
            )

            source_ids.add(source_id)

            document = self._find_document_from_cache(
                row=row,
                documents_by_folio=documents_by_folio,
            )

            if not document:
                invalid_match += 1
                continue

            amount = self._decimal(
                row["Total_Recon_Manual"]
            )

            if amount == Decimal("0"):
                skipped_zero += 1
                continue

            existing = existing_by_source_id.get(
                source_id
            )

            invoice_number = str(
                row["FolioNum"]
            ).strip()

            source_rut = self._clean(
                row["Rut"]
            )

            timeline_order_at = (
                self._aware_datetime(
                    row["Fecha_Carga"]
                )
            )

            if existing is None:
                to_create.append(
                    ManualReconciliationApplication(
                        source_id=row["Id_PK"],
                        document=document,
                        customer=document.customer,
                        invoice_number=invoice_number,
                        source_rut=source_rut,
                        amount=amount,
                        timeline_order_at=(
                            timeline_order_at
                        ),
                        source_table=self.SOURCE_TABLE,
                    )
                )

                affected_document_ids.add(
                    document.id
                )

                created += 1
                continue

            changed = (
                existing.document_id
                != document.id
                or existing.customer_id
                != document.customer_id
                or existing.invoice_number
                != invoice_number
                or existing.source_rut
                != source_rut
                or existing.amount
                != amount
                or existing.timeline_order_at
                != timeline_order_at
                or existing.source_table
                != self.SOURCE_TABLE
            )

            if not changed:
                unchanged += 1
                continue

            # Una reconciliación puede cambiar de factura
            # después de una recarga de fuente.
            affected_document_ids.add(
                existing.document_id
            )
            affected_document_ids.add(
                document.id
            )

            existing.document = document
            existing.customer = document.customer
            existing.invoice_number = invoice_number
            existing.source_rut = source_rut
            existing.amount = amount
            existing.timeline_order_at = (
                timeline_order_at
            )
            existing.source_table = (
                self.SOURCE_TABLE
            )

            to_update.append(existing)
            updated += 1

        # ------------------------------------------------------------
        # IMPORTANTE:
        #
        # Esta tabla puede ser recargada.
        # Si una identidad local desapareció del snapshot fuente,
        # debe eliminarse y recalcular su documento.
        # ------------------------------------------------------------

        local_source_ids = set(
            existing_by_source_id.keys()
        )

        removed_source_ids = (
            local_source_ids - source_ids
        )

        delete_ids = []

        for source_id in removed_source_ids:
            existing = existing_by_source_id[
                source_id
            ]

            affected_document_ids.add(
                existing.document_id
            )

            delete_ids.append(
                existing.id
            )

            deleted += 1

        documents_with_financial_effect = 0
        documents_without_financial_effect = 0

        with transaction.atomic():
            if delete_ids:
                self._delete_in_batches(
                    delete_ids
                )

            if to_create:
                (
                    ManualReconciliationApplication
                    .objects
                    .bulk_create(
                        to_create,
                        batch_size=self.BATCH_SIZE,
                    )
                )

            if to_update:
                (
                    ManualReconciliationApplication
                    .objects
                    .bulk_update(
                        to_update,
                        fields=[
                            "document",
                            "customer",
                            "invoice_number",
                            "source_rut",
                            "amount",
                            "timeline_order_at",
                            "source_table",
                        ],
                        batch_size=self.BATCH_SIZE,
                    )
                )

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
            "Reconciliaciones actualizadas realmente: "
            f"{updated}"
        )

        self.stdout.write(
            f"Reconciliaciones sin cambios: {unchanged}"
        )

        self.stdout.write(
            "Reconciliaciones eliminadas por "
            f"desaparición de fuente: {deleted}"
        )

        self.stdout.write(
            f"Sin factura válida: {invalid_match}"
        )

        self.stdout.write(
            f"Filas monto cero: {skipped_zero}"
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

    def _find_document_from_cache(
        self,
        row,
        documents_by_folio,
    ):
        folio = str(
            row["FolioNum"]
        ).strip()

        source_rut_body = self._rut_body(
            row["Rut"]
        )

        if not source_rut_body:
            return None

        candidates = documents_by_folio.get(
            folio,
            [],
        )

        matches = [
            document
            for document in candidates
            if self._rut_body(
                document.customer.rut
            )
            == source_rut_body
        ]

        # Mantener exactamente la regla anterior:
        # solo se acepta match único.
        if len(matches) != 1:
            return None

        return matches[0]

    def _delete_in_batches(self, ids):
        ids = list(ids)

        for offset in range(
            0,
            len(ids),
            self.BATCH_SIZE,
        ):
            batch = ids[
                offset:
                offset + self.BATCH_SIZE
            ]

            (
                ManualReconciliationApplication
                .objects
                .filter(id__in=batch)
                .delete()
            )

    def _rut_body(self, value):
        value = self._clean(value)

        if not value:
            return ""

        if "-" in value:
            return value.split(
                "-",
                1,
            )[0].strip()

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
