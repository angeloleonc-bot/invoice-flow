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
    help = (
        "Sincroniza pagos desde Pago_Vta_Reg y recalcula "
        "saldos solo para documentos realmente afectados."
    )

    BATCH_SIZE = 500

    def handle(self, *args, **options):
        rows = self._fetch_rows()

        created = 0
        updated = 0
        unchanged = 0
        deleted_cancelled = 0
        missing_documents = 0
        skipped = 0

        affected_document_ids = set()

        # ------------------------------------------------------------
        # Cargar una sola vez el universo de documentos candidatos.
        #
        # Conserva la lógica histórica:
        # 1. buscar factura por folio;
        # 2. si hay Rut, preferir customer.external_id == Rut;
        # 3. si no existe ese match, usar el primer documento por id.
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

        documents_by_folio = {}

        for document in documents:
            folio = self._clean(document.document_number)

            documents_by_folio.setdefault(
                folio,
                [],
            ).append(document)

        # ------------------------------------------------------------
        # Cargar una sola vez los PaymentRecord ya importados.
        # No utilizamos un IN gigante para evitar límites de parámetros
        # de SQL Server.
        # ------------------------------------------------------------

        existing_payments = {
            payment.external_payment_id: payment
            for payment in (
                PaymentRecord.objects
                .filter(
                    source_table=Document.SOURCE_PAGO_VTA_REG,
                )
                .select_related("document")
            )
        }

        to_create = []
        to_update = []
        delete_ids = []

        # ------------------------------------------------------------
        # Preparación en memoria.
        # No hay escrituras todavía.
        # ------------------------------------------------------------

        for row in rows:
            external_payment_id = (
                f"Pago_Vta_Reg:{row['Id_PK']}"
            )

            existing = existing_payments.get(
                external_payment_id
            )

            cancelada = (
                self._clean(row["Cancelada"]).upper()
            )

            # --------------------------------------------------------
            # Cancelación
            # --------------------------------------------------------

            if cancelada == "Y":
                if existing:
                    affected_document_ids.add(
                        existing.document_id
                    )
                    delete_ids.append(existing.id)
                    deleted_cancelled += 1

                continue

            # --------------------------------------------------------
            # Resolver Document en memoria
            # --------------------------------------------------------

            document = self._find_document_from_cache(
                row=row,
                documents_by_folio=documents_by_folio,
            )

            if not document:
                missing_documents += 1
                continue

            amount = self._decimal(
                row["Pagado_MS"]
            )

            if amount == Decimal("0"):
                skipped += 1
                continue

            payment_date = self._parse_date(
                row["Fecha_Contabilizacion"]
            )

            source_reference = str(
                row["Pago_Recibido"]
            )

            notes = self._build_notes(row)

            # --------------------------------------------------------
            # Nuevo
            # --------------------------------------------------------

            if existing is None:
                to_create.append(
                    PaymentRecord(
                        document=document,
                        customer=document.customer,
                        payment_date=payment_date,
                        amount=amount,
                        source_reference=source_reference,
                        external_payment_id=external_payment_id,
                        source_table=(
                            Document.SOURCE_PAGO_VTA_REG
                        ),
                        notes=notes,
                    )
                )

                affected_document_ids.add(
                    document.id
                )

                created += 1
                continue

            # --------------------------------------------------------
            # Existente: comparar antes de escribir.
            # --------------------------------------------------------

            changed = (
                existing.document_id != document.id
                or existing.customer_id != document.customer_id
                or existing.payment_date != payment_date
                or existing.amount != amount
                or existing.source_reference != source_reference
                or existing.source_table
                != Document.SOURCE_PAGO_VTA_REG
                or existing.notes != notes
            )

            if not changed:
                unchanged += 1
                continue

            # Si una corrección cambia de documento, ambos deben
            # recalcularse: el anterior y el nuevo.
            affected_document_ids.add(
                existing.document_id
            )
            affected_document_ids.add(
                document.id
            )

            existing.document = document
            existing.customer = document.customer
            existing.payment_date = payment_date
            existing.amount = amount
            existing.source_reference = source_reference
            existing.source_table = (
                Document.SOURCE_PAGO_VTA_REG
            )
            existing.notes = notes

            to_update.append(existing)
            updated += 1

        # ------------------------------------------------------------
        # Persistencia.
        #
        # La parte de lectura/comparación ocurre fuera de atomic.
        # La transacción cubre únicamente cambios reales.
        # ------------------------------------------------------------

        with transaction.atomic():
            if delete_ids:
                self._delete_in_batches(
                    delete_ids
                )

            if to_create:
                PaymentRecord.objects.bulk_create(
                    to_create,
                    batch_size=self.BATCH_SIZE,
                )

            if to_update:
                PaymentRecord.objects.bulk_update(
                    to_update,
                    fields=[
                        "document",
                        "customer",
                        "payment_date",
                        "amount",
                        "source_reference",
                        "source_table",
                        "notes",
                    ],
                    batch_size=self.BATCH_SIZE,
                )

            # Mantener de momento el mismo servicio financiero.
            # En esta etapa solo cambia CUÁNDO se llama:
            # exclusivamente ante cambios reales.
            for document_id in affected_document_ids:
                recalculate_document_financial_state(
                    document_id
                )

        self.stdout.write(
            self.style.SUCCESS(
                "Sincronización Pago_Vta_Reg completada."
            )
        )

        self.stdout.write(
            f"Filas fuente: {len(rows)}"
        )

        self.stdout.write(
            f"Pagos creados: {created}"
        )

        self.stdout.write(
            f"Pagos actualizados realmente: {updated}"
        )

        self.stdout.write(
            f"Pagos sin cambios: {unchanged}"
        )

        self.stdout.write(
            "Pagos cancelados eliminados: "
            f"{deleted_cancelled}"
        )

        self.stdout.write(
            f"Facturas no encontradas: {missing_documents}"
        )

        self.stdout.write(
            f"Filas omitidas: {skipped}"
        )

        self.stdout.write(
            "Facturas recalculadas: "
            f"{len(affected_document_ids)}"
        )

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

        rut = self._clean(
            row["Rut"]
        )

        candidates = documents_by_folio.get(
            folio,
            [],
        )

        if not candidates:
            return None

        # Preserva exactamente el comportamiento anterior:
        # primero customer.external_id == rut.
        if rut:
            rut_lower = rut.lower()

            for document in candidates:
                external_id = self._clean(
                    document.customer.external_id
                )

                if external_id.lower() == rut_lower:
                    return document

        # Fallback histórico: primer documento por id.
        return candidates[0]

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

            PaymentRecord.objects.filter(
                id__in=batch
            ).delete()

    def _parse_date(self, value):
        if isinstance(value, datetime):
            return value.date()

        value = self._clean(value)

        if not value:
            return datetime.today().date()

        for fmt in (
            "%d/%m/%Y",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(
                    value,
                    fmt,
                ).date()
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
            (
                "Asiento PRE: "
                f"{self._clean(row['Asiento_PRE'])}"
            ),
            (
                "DocTransId PRE: "
                f"{self._clean(row['DocTransId_PRE'])}"
            ),
            (
                "DocEntry: "
                f"{self._clean(row['DocEntry'])}"
            ),
            (
                "Comentario: "
                f"{self._clean(row['Comentario'])}"
            ),
            (
                "Moneda pago: "
                f"{self._clean(row['Moneda_PRE'])}"
            ),
            (
                "Moneda factura: "
                f"{self._clean(row['Moneda_FA'])}"
            ),
        ]

        return "\n".join(
            part
            for part in parts
            if part
            and not part.endswith(": ")
        )
