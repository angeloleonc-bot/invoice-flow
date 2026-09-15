from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from apps.portfolio.models import (
    CreditNoteApplication,
    Document,
)
from apps.portfolio.services.financial import (
    recalculate_document_financial_state,
)


class Command(BaseCommand):
    help = (
        "Sincroniza notas de crédito desde NC_Vta_Reg1 "
        "y recalcula solo facturas realmente afectadas."
    )

    BATCH_SIZE = 500

    def handle(self, *args, **options):
        rows = self._fetch_rows()

        created = 0
        updated = 0
        unchanged = 0
        missing_documents = 0

        affected_document_ids = set()

        # ------------------------------------------------------------
        # Documentos candidatos en memoria.
        # NC históricamente hace match por document_number = FolioDB.
        # ------------------------------------------------------------

        documents = list(
            Document.objects
            .filter(
                external_source=Document.SOURCE_FACT_VTA_REG,
                document_type=Document.DOCUMENT_TYPE_INVOICE,
            )
            .select_related("customer")
            .order_by("id")
        )

        documents_by_number = {}

        for document in documents:
            key = str(
                document.document_number
            ).strip()

            # El comando anterior hacía .first(), por lo que
            # conservamos el primer id.
            if key not in documents_by_number:
                documents_by_number[key] = document

        # ------------------------------------------------------------
        # NC existentes en memoria.
        #
        # Identidad histórica:
        # credit_trans_id + document.
        # ------------------------------------------------------------

        existing_notes = {}

        for note in (
            CreditNoteApplication.objects
            .select_related(
                "document",
                "customer",
            )
            .all()
        ):
            key = (
                str(note.credit_trans_id),
                note.document_id,
            )

            existing_notes[key] = note

        to_create = []
        to_update = []

        for row in rows:
            document_number = str(
                row["FolioDB"]
            ).strip()

            document = documents_by_number.get(
                document_number
            )

            if not document:
                missing_documents += 1
                continue

            credit_trans_id = str(
                row["TransId"]
            )

            key = (
                credit_trans_id,
                document.id,
            )

            existing = existing_notes.get(key)

            values = {
                "customer_id": document.customer_id,
                "credit_document_number": str(
                    row["Num_doc"]
                ),
                "source_base_folio": self._clean(
                    row["Folio_Base"]
                ),
                "credit_doc_entry": self._clean(
                    row["DocEntry"]
                ),
                "target_invoice_number": str(
                    row["FolioDB"]
                ),
                "reference_type": self._clean(
                    row["Tipo_Ref"]
                ),
                "credit_type": self._clean(
                    row["Tipo"]
                ),
                "credit_amount": self._decimal(
                    row["NC_Monto_Total"]
                ),
                "invoice_amount": self._decimal(
                    row["FAC_Monto_Total"]
                ),
                "status": self._clean(
                    row["Estado"]
                ),
                "reason": self._clean(
                    row["Causa"]
                ),
                "comment": self._clean(
                    row["Comentario"]
                ),
                "issue_date": (
                    row["Fecha_Documento"].date()
                    if row["Fecha_Documento"]
                    else None
                ),
                "source_snapshot_date": (
                    self._aware_datetime(
                        row["Fecha_Informe"]
                    )
                ),
            }

            if existing is None:
                to_create.append(
                    CreditNoteApplication(
                        credit_trans_id=credit_trans_id,
                        document=document,
                        customer=document.customer,
                        credit_document_number=(
                            values[
                                "credit_document_number"
                            ]
                        ),
                        source_base_folio=(
                            values[
                                "source_base_folio"
                            ]
                        ),
                        credit_doc_entry=(
                            values[
                                "credit_doc_entry"
                            ]
                        ),
                        target_invoice_number=(
                            values[
                                "target_invoice_number"
                            ]
                        ),
                        reference_type=(
                            values[
                                "reference_type"
                            ]
                        ),
                        credit_type=(
                            values[
                                "credit_type"
                            ]
                        ),
                        credit_amount=(
                            values[
                                "credit_amount"
                            ]
                        ),
                        invoice_amount=(
                            values[
                                "invoice_amount"
                            ]
                        ),
                        status=values["status"],
                        reason=values["reason"],
                        comment=values["comment"],
                        issue_date=(
                            values["issue_date"]
                        ),
                        source_snapshot_date=(
                            values[
                                "source_snapshot_date"
                            ]
                        ),
                    )
                )

                affected_document_ids.add(
                    document.id
                )
                created += 1
                continue

            changed = (
                existing.customer_id
                != values["customer_id"]
                or existing.credit_document_number
                != values["credit_document_number"]
                or existing.source_base_folio
                != values["source_base_folio"]
                or existing.credit_doc_entry
                != values["credit_doc_entry"]
                or existing.target_invoice_number
                != values["target_invoice_number"]
                or existing.reference_type
                != values["reference_type"]
                or existing.credit_type
                != values["credit_type"]
                or existing.credit_amount
                != values["credit_amount"]
                or existing.invoice_amount
                != values["invoice_amount"]
                or existing.status
                != values["status"]
                or existing.reason
                != values["reason"]
                or existing.comment
                != values["comment"]
                or existing.issue_date
                != values["issue_date"]
                or existing.source_snapshot_date
                != values["source_snapshot_date"]
            )

            if not changed:
                unchanged += 1
                continue

            existing.customer_id = (
                values["customer_id"]
            )
            existing.credit_document_number = (
                values["credit_document_number"]
            )
            existing.source_base_folio = (
                values["source_base_folio"]
            )
            existing.credit_doc_entry = (
                values["credit_doc_entry"]
            )
            existing.target_invoice_number = (
                values["target_invoice_number"]
            )
            existing.reference_type = (
                values["reference_type"]
            )
            existing.credit_type = (
                values["credit_type"]
            )
            existing.credit_amount = (
                values["credit_amount"]
            )
            existing.invoice_amount = (
                values["invoice_amount"]
            )
            existing.status = values["status"]
            existing.reason = values["reason"]
            existing.comment = values["comment"]
            existing.issue_date = (
                values["issue_date"]
            )
            existing.source_snapshot_date = (
                values["source_snapshot_date"]
            )

            to_update.append(existing)

            affected_document_ids.add(
                document.id
            )

            updated += 1

        with transaction.atomic():
            if to_create:
                CreditNoteApplication.objects.bulk_create(
                    to_create,
                    batch_size=self.BATCH_SIZE,
                )

            if to_update:
                CreditNoteApplication.objects.bulk_update(
                    to_update,
                    fields=[
                        "customer",
                        "credit_document_number",
                        "source_base_folio",
                        "credit_doc_entry",
                        "target_invoice_number",
                        "reference_type",
                        "credit_type",
                        "credit_amount",
                        "invoice_amount",
                        "status",
                        "reason",
                        "comment",
                        "issue_date",
                        "source_snapshot_date",
                    ],
                    batch_size=self.BATCH_SIZE,
                )

            for document_id in affected_document_ids:
                recalculate_document_financial_state(
                    document_id
                )

        self.stdout.write(
            self.style.SUCCESS(
                "Sincronización NC_Vta_Reg1 completada."
            )
        )

        self.stdout.write(
            f"Filas fuente: {len(rows)}"
        )

        self.stdout.write(
            f"NC creadas: {created}"
        )

        self.stdout.write(
            f"NC actualizadas realmente: {updated}"
        )

        self.stdout.write(
            f"NC sin cambios: {unchanged}"
        )

        self.stdout.write(
            "Facturas no encontradas: "
            f"{missing_documents}"
        )

        self.stdout.write(
            "Facturas recalculadas: "
            f"{len(affected_document_ids)}"
        )

    def _fetch_rows(self):
        query = """
            SELECT
                TransId,
                Num_doc,
                Folio_Base,
                DocEntry,
                FolioDB,
                Tipo_Ref,
                Fecha_Documento,
                Id,
                Rut,
                Nombre,
                Telefono,
                E_mail,
                Tipo,
                NC_Monto_Total,
                FAC_Monto_Total,
                Estado,
                Causa,
                Comentario,
                Fecha_Informe
            FROM [dbo].[NC_Vta_Reg1]
            WHERE
                TransId IS NOT NULL
                AND Num_doc IS NOT NULL
                AND FolioDB IS NOT NULL
                AND NC_Monto_Total IS NOT NULL
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

    def _decimal(self, value):
        if value is None:
            return Decimal("0")

        return Decimal(value)

    def _aware_datetime(self, value):
        if value is None:
            return None

        if timezone.is_naive(value):
            return timezone.make_aware(value)

        return value

    def _clean(self, value):
        if value is None:
            return ""

        return str(value).strip()
