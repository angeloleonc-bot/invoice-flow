from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from apps.portfolio.models import CreditNoteApplication, Document

from apps.portfolio.services.financial import (
    recalculate_document_financial_state,
)


class Command(BaseCommand):
    help = "Sincroniza notas de crédito desde NC_Vta_Reg1 y actualiza saldos de facturas."

    def handle(self, *args, **options):
        rows = self._fetch_rows()

        created = 0
        updated = 0
        missing_documents = 0
        recalculated_documents = set()

        with transaction.atomic():
            for row in rows:
                document = (
                    Document.objects
                    .select_related("customer")
                    .filter(
                        external_source=Document.SOURCE_FACT_VTA_REG,
                        document_type=Document.DOCUMENT_TYPE_INVOICE,
                        document_number=str(row["FolioDB"]),
                    )
                    .first()
                )

                if not document:
                    missing_documents += 1
                    continue

                obj, was_created = CreditNoteApplication.objects.update_or_create(
                    credit_trans_id=str(row["TransId"]),
                    document=document,
                    defaults={
                        "customer": document.customer,
                        "credit_document_number": str(row["Num_doc"]),
                        "source_base_folio": self._clean(row["Folio_Base"]),
                        "credit_doc_entry": self._clean(row["DocEntry"]),
                        "target_invoice_number": str(row["FolioDB"]),
                        "reference_type": self._clean(row["Tipo_Ref"]),
                        "credit_type": self._clean(row["Tipo"]),
                        "credit_amount": self._decimal(row["NC_Monto_Total"]),
                        "invoice_amount": self._decimal(row["FAC_Monto_Total"]),
                        "status": self._clean(row["Estado"]),
                        "reason": self._clean(row["Causa"]),
                        "comment": self._clean(row["Comentario"]),
                        "issue_date": row["Fecha_Documento"].date() if row["Fecha_Documento"] else None,
                        "source_snapshot_date": self._aware_datetime(row["Fecha_Informe"]),
                    },
                )

                if was_created:
                    created += 1
                else:
                    updated += 1

                recalculated_documents.add(document.id)

            for document_id in recalculated_documents:
                recalculate_document_financial_state(document_id)

        self.stdout.write(self.style.SUCCESS("Sincronización NC_Vta_Reg1 completada."))
        self.stdout.write(f"Filas fuente: {len(rows)}")
        self.stdout.write(f"NC creadas: {created}")
        self.stdout.write(f"NC actualizadas: {updated}")
        self.stdout.write(f"Facturas no encontradas: {missing_documents}")
        self.stdout.write(f"Facturas recalculadas: {len(recalculated_documents)}")

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
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]


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