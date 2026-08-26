from datetime import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import connection, transaction


from apps.portfolio.models import (
    Document,
    PaymentRecord,
)

from apps.portfolio.services.financial import (
    recalculate_document_financial_state,
)

class Command(BaseCommand):
    help = "Sincroniza pagos desde Pago_Vta_Reg y recalcula saldos de documentos."

    def handle(self, *args, **options):
        rows = self._fetch_rows()

        created = 0
        updated = 0
        deleted_cancelled = 0
        missing_documents = 0
        skipped = 0
        affected_document_ids = set()

        with transaction.atomic():
            for row in rows:
                external_payment_id = f"Pago_Vta_Reg:{row['Id_PK']}"
                cancelada = self._clean(row["Cancelada"]).upper()

                if cancelada == "Y":
                    existing = PaymentRecord.objects.filter(
                        external_payment_id=external_payment_id,
                        source_table=Document.SOURCE_PAGO_VTA_REG,
                    ).select_related("document").first()

                    if existing:
                        affected_document_ids.add(existing.document_id)
                        existing.delete()
                        deleted_cancelled += 1

                    continue

                document = self._find_document(row)

                if not document:
                    missing_documents += 1
                    continue

                amount = self._decimal(row["Pagado_MS"])

                if amount == Decimal("0"):
                    skipped += 1
                    continue

                payment_date = self._parse_date(row["Fecha_Contabilizacion"])

                obj, was_created = PaymentRecord.objects.update_or_create(
                    external_payment_id=external_payment_id,
                    defaults={
                        "document": document,
                        "customer": document.customer,
                        "payment_date": payment_date,
                        "amount": amount,
                        "source_reference": str(row["Pago_Recibido"]),
                        "source_table": Document.SOURCE_PAGO_VTA_REG,
                        "notes": self._build_notes(row),
                    },
                )

                if was_created:
                    created += 1
                else:
                    updated += 1

                affected_document_ids.add(document.id)

            for document_id in affected_document_ids:
                recalculate_document_financial_state(document_id)

        self.stdout.write(self.style.SUCCESS("Sincronización Pago_Vta_Reg completada."))
        self.stdout.write(f"Filas fuente: {len(rows)}")
        self.stdout.write(f"Pagos creados: {created}")
        self.stdout.write(f"Pagos actualizados: {updated}")
        self.stdout.write(f"Pagos cancelados eliminados: {deleted_cancelled}")
        self.stdout.write(f"Facturas no encontradas: {missing_documents}")
        self.stdout.write(f"Filas omitidas: {skipped}")
        self.stdout.write(f"Facturas recalculadas: {len(affected_document_ids)}")

    def _fetch_rows(self):
        query = """
            SELECT
                Id_PK,
                Pago_Recibido,
                Fecha_Contabilizacion,
                Fecha_Creacion,
                Cancelada,
                Asiento_PRE,
                ObjType,
                Rut,
                DocTransId_PRE,
                Tipo,
                FolioNum,
                DocEntry,
                Pagado_MS,
                Pagado_ME,
                Comentario,
                Moneda_PRE,
                TipoCambio_PRE,
                Moneda_FA,
                TipoCambio_FA,
                Moneda_DocTransId
            FROM [dbo].[Pago_Vta_Reg]
            WHERE
                Tipo = 'FA'
                AND Id_PK IS NOT NULL
                AND Pago_Recibido IS NOT NULL
                AND FolioNum IS NOT NULL
                AND Pagado_MS IS NOT NULL
        """

        with connection.cursor() as cursor:
            cursor.execute(query)
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _find_document(self, row):
        folio = str(row["FolioNum"]).strip()
        rut = self._clean(row["Rut"])

        qs = Document.objects.select_related("customer").filter(
            external_source=Document.SOURCE_FACT_VTA_REG,
            document_type=Document.DOCUMENT_TYPE_INVOICE,
            document_number=folio,
        )

        if rut:
            document = qs.filter(customer__external_id=rut).order_by("id").first()
            if document:
                return document

        return qs.order_by("id").first()


    def _parse_date(self, value):
        if isinstance(value, datetime):
            return value.date()

        value = self._clean(value)

        if not value:
            return datetime.today().date()

        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue

        return datetime.today().date()

    def _decimal(self, value):
        if value is None:
            return Decimal("0")

        return Decimal(str(value))

    def _clean(self, value):
        if value is None:
            return ""

        return str(value).strip()

    def _build_notes(self, row):
        parts = [
            f"Pago recibido: {row['Pago_Recibido']}",
            f"Folio factura: {row['FolioNum']}",
            f"Asiento PRE: {self._clean(row['Asiento_PRE'])}",
            f"DocTransId PRE: {self._clean(row['DocTransId_PRE'])}",
            f"DocEntry: {self._clean(row['DocEntry'])}",
            f"Comentario: {self._clean(row['Comentario'])}",
            f"Moneda pago: {self._clean(row['Moneda_PRE'])}",
            f"Moneda factura: {self._clean(row['Moneda_FA'])}",
        ]

        return "\n".join(part for part in parts if part and not part.endswith(": "))