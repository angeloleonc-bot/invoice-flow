from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import Coalesce

from apps.portfolio.models import (
    CreditNoteApplication,
    Document,
    DocumentStatus,
    DocumentSubStatus,
    ManualReconciliationApplication,
    PaymentRecord,
)


ZERO = Decimal("0")
ROUNDING_TOLERANCE = Decimal("1.00")


def _find_active_status(name):
    return (
        DocumentStatus.objects
        .filter(
            name__iexact=name,
            is_active=True,
        )
        .first()
    )


def _find_active_sub_status(name):
    return (
        DocumentSubStatus.objects
        .filter(
            name__iexact=name,
            is_active=True,
        )
        .first()
    )


def get_document_financial_totals(document):
    total_credit_notes = (
        CreditNoteApplication.objects
        .filter(document=document)
        .aggregate(
            total=Coalesce(
                Sum("credit_amount"),
                ZERO,
            )
        )["total"]
    )

    total_paid = (
        PaymentRecord.objects
        .filter(document=document)
        .aggregate(
            total=Coalesce(
                Sum("amount"),
                ZERO,
            )
        )["total"]
    )

    total_manual_reconciliations = (
        ManualReconciliationApplication.objects
        .filter(document=document)
        .aggregate(
            total=Coalesce(
                Sum("amount"),
                ZERO,
            )
        )["total"]
    )

    return {
        "total_credit_notes": total_credit_notes,
        "total_paid": total_paid,
        "total_manual_reconciliations": (
            total_manual_reconciliations
        ),
    }


def recalculate_document_financial_state(document_id):
    document = (
        Document.objects
        .select_related(
            "status",
            "sub_status",
        )
        .get(id=document_id)
    )

    totals = get_document_financial_totals(document)

    total_credit_notes = totals["total_credit_notes"]
    total_paid = totals["total_paid"]
    total_manual = totals["total_manual_reconciliations"]

    # Total_Recon_Manual representa el total reconciliado
    # informado por la fuente e incluye las NC aplicadas.
    #
    # Por lo tanto, solo la parte que excede las NC
    # constituye reconciliación manual adicional.
    manual_net = max(
        total_manual - total_credit_notes,
        ZERO,
    )

    balance_before_manual = (
        document.original_amount
        - total_credit_notes
        - total_paid
    )

    # La reconciliación manual nunca puede generar
    # un saldo a favor del cliente.
    manual_applicable = min(
        manual_net,
        max(balance_before_manual, ZERO),
    )

    raw_balance = (
        balance_before_manual
        - manual_applicable
    )

    if raw_balance < ZERO:
        new_balance = ZERO
        overpayment_amount = abs(raw_balance)
    else:
        new_balance = raw_balance
        overpayment_amount = ZERO

    if overpayment_amount <= ROUNDING_TOLERANCE:
        overpayment_amount = ZERO

    document.balance_amount = new_balance
    document.overpayment_amount = overpayment_amount

    update_fields = [
        "balance_amount",
        "overpayment_amount",
        "updated_at",
    ]

    paid_status = _find_active_status("Pagada")
    closed_status = _find_active_status("Cerrada")
    pending_status = _find_active_status("Pendiente")

    paid_substatus = _find_active_sub_status(
        "Pago total informado"
    )
    partial_payment_substatus = _find_active_sub_status(
        "Pago parcial informado"
    )
    covered_by_nc_substatus = _find_active_sub_status(
        "Cubierto por NC"
    )
    overpayment_substatus = _find_active_sub_status(
        "Saldo a favor cliente"
    )

    manual_reconciliation_substatus = _find_active_sub_status(
        "Cubierto por reconciliación manual"
    )

    if new_balance <= ZERO:

        if overpayment_amount > ZERO:
            if total_paid > ZERO and paid_status:
                document.status = paid_status
                update_fields.append("status")
            elif closed_status:
                document.status = closed_status
                update_fields.append("status")

            if overpayment_substatus:
                document.sub_status = overpayment_substatus
                update_fields.append("sub_status")

        elif total_paid > ZERO:
            if paid_status:
                document.status = paid_status
                update_fields.append("status")

            # Cierre exclusivamente por pago.
            if (
                total_credit_notes == ZERO
                and total_manual == ZERO
                and paid_substatus
            ):
                document.sub_status = paid_substatus
                update_fields.append("sub_status")

            # Cierre mixto: no etiquetar como "Pago total informado".
            elif (
                document.sub_status
                and document.sub_status.name.casefold()
                in {
                    "pago total informado",
                    "cubierto por nc",
                    "cubierto por reconciliación manual",
                    "saldo a favor cliente",
                }
            ):
                document.sub_status = None
                update_fields.append("sub_status")

        elif (
            total_manual > ZERO
            and total_credit_notes == ZERO
        ):
            if closed_status:
                document.status = closed_status
                update_fields.append("status")

            if manual_reconciliation_substatus:
                document.sub_status = manual_reconciliation_substatus
                update_fields.append("sub_status")

        elif total_credit_notes >= document.original_amount:
            if closed_status:
                document.status = closed_status
                update_fields.append("status")

            if covered_by_nc_substatus:
                document.sub_status = covered_by_nc_substatus
                update_fields.append("sub_status")

        else:
            # Cierre mixto NC + reconciliación manual.
            if closed_status:
                document.status = closed_status
                update_fields.append("status")

            if (
                document.sub_status
                and document.sub_status.name.casefold()
                in {
                    "pago total informado",
                    "cubierto por nc",
                    "cubierto por reconciliación manual",
                    "saldo a favor cliente",
                }
            ):
                document.sub_status = None
                update_fields.append("sub_status")

    else:
        if total_paid > ZERO and partial_payment_substatus:
            document.sub_status = partial_payment_substatus
            update_fields.append("sub_status")

        elif (
            document.sub_status
            and document.sub_status.name.casefold()
            in {
                "pago total informado",
                "cubierto por nc",
                "cubierto por reconciliación manual",
                "saldo a favor cliente",
            }
        ):
            document.sub_status = None
            update_fields.append("sub_status")

        if (
            document.status
            and document.status.name.casefold()
            in {"pagada", "cerrada"}
            and pending_status
        ):
            document.status = pending_status
            update_fields.append("status")

    document.save(
        update_fields=list(dict.fromkeys(update_fields))
    )

    return {
        "document": document,
        "total_credit_notes": total_credit_notes,
        "total_paid": total_paid,
        "total_manual_reconciliations": total_manual,
        "manual_reconciliation_net": manual_net,
        "manual_reconciliation_applied": manual_applicable,
        "balance_amount": new_balance,
        "overpayment_amount": overpayment_amount,
    }