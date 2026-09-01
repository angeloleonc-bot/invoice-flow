from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import (
    Http404,
    HttpResponse,
    JsonResponse,
)
from django.shortcuts import (
    get_object_or_404,
    redirect,
    render,
)
from django.urls import reverse
from django.utils import timezone

from apps.accounts.services.role_service import RoleService
from apps.accounts.services.delegated_token_cache import (
    DelegatedTokenCacheService,
)
from apps.accounts.services.delegated_graph import (
    DelegatedGraphAuthenticationError,
    DelegatedGraphProviderError,
    DelegatedGraphUnavailableError,
)
from apps.portfolio.models import (
    Customer,
    CustomerStatement,
    DocumentAssignment,
)
from apps.portfolio.services.customer_statements import (
    CATEGORY_DUE_TODAY,
    CATEGORY_OVERDUE,
    CATEGORY_UPCOMING,
    CustomerStatementService,
)
from apps.portfolio.services.statement_exports import (
    CustomerStatementExportService,
)
from apps.portfolio.services.statement_delivery import (
    CustomerStatementDeliveryService,
    StatementDeliveryError,
    StatementFinancialStateChangedError,
    StatementNotSendableError,
    StatementSenderMismatchError,
)
from apps.portfolio.services.statement_preview import (
    CustomerStatementPreviewService,
)


STATEMENT_ALLOWED_ROLES = {
    "ADMINISTRADOR",
    "SUPERVISOR",
    "COBRADOR",
}


def _can_send_statement(user, customer: Customer) -> bool:

    if not user or not user.is_authenticated:
        return False

    role = RoleService.get_effective_role_code(
        user
    )

    if role not in STATEMENT_ALLOWED_ROLES:
        return False

    if role in {
        "ADMINISTRADOR",
        "SUPERVISOR",
    }:
        return True

    return DocumentAssignment.objects.filter(
        document__customer=customer,
        assigned_to=user,
        is_active=True,
    ).exists()


def _get_statement_for_user(
    *,
    public_id,
    user,
) -> CustomerStatement:

    statement = get_object_or_404(
        CustomerStatement.objects.select_related(
            "customer",
            "created_by",
        ),
        public_id=public_id,
    )

    if statement.created_by_id != user.id:
        role = RoleService.get_effective_role_code(
            user
        )

        if role not in {
            "ADMINISTRADOR",
            "SUPERVISOR",
        }:
            raise Http404

    return statement


@login_required
def customer_statement_form(
    request,
    customer_id,
):

    customer = get_object_or_404(
        Customer,
        id=customer_id,
    )

    if not _can_send_statement(
        request.user,
        customer,
    ):
        return HttpResponse(
            "No tiene permisos para enviar "
            "estados de cuenta de este cliente.",
            status=403,
        )

    documents = (
        CustomerStatementService.available_documents(
            customer=customer
        )
    )

    category_definitions = [
        (
            CATEGORY_OVERDUE,
            "Vencidos",
        ),
        (
            CATEGORY_DUE_TODAY,
            "Vence hoy",
        ),
        (
            CATEGORY_UPCOMING,
            "Por vencer",
        ),
    ]

    groups = []

    for code, label in category_definitions:
        category_documents = [
            document
            for document in documents
            if document.category == code
        ]

        groups.append(
            {
                "code": code,
                "label": label,
                "documents": category_documents,
                "count": len(
                    category_documents
                ),
                "total": sum(
                    (
                        document.balance_amount
                        for document
                        in category_documents
                    ),
                    Decimal("0"),
                ),
            }
        )

    groups = [
        group
        for group in groups
        if group["count"] > 0
    ]

    contacts = list(
        customer.contacts
        .filter(
            do_not_contact=False,
        )
        .exclude(email="")
        .order_by(
            "-is_primary",
            "name",
            "email",
        )
    )

    suggested_emails = []

    for contact in contacts:
        email = str(
            contact.email or ""
        ).strip().lower()

        if email and email not in suggested_emails:
            suggested_emails.append(email)

    customer_email = str(
        customer.email or ""
    ).strip().lower()

    if (
        customer_email
        and customer_email
        not in suggested_emails
    ):
        suggested_emails.append(
            customer_email
        )

    default_to = (
        suggested_emails[0]
        if suggested_emails
        else ""
    )

    today = timezone.localdate()

    return render(
        request,
        "portfolio/statements/"
        "_statement_modal_content.html",
        {
            "customer": customer,
            "groups": groups,
            "statement_document_count": len(
                documents
            ),
            "suggested_emails": (
                suggested_emails
            ),
            "default_to": default_to,
            "sender_email": (
                request.user.email
            ),
            "default_subject": (
                "Estado de Cuenta - "
                f"{customer.name} - "
                f"{today.strftime('%d/%m/%Y')}"
            ),
            "default_message_body": (
                "Estimados,\n\n"
                "Junto con saludar, adjuntamos el estado de cuenta "
                "actualizado, con el detalle de los documentos "
                "pendientes a la fecha.\n\n"
                "Agradecemos revisar la información y, ante cualquier "
                "consulta o diferencia, responder a este correo para "
                "poder revisarla.\n\n"
                "Saludos cordiales."
            ),
        },
    )


@login_required
def customer_statement_create_preview(
    request,
    customer_id,
):

    if request.method != "POST":
        return redirect(
            "portfolio:customer_detail",
            customer_id=customer_id,
        )

    customer = get_object_or_404(
        Customer,
        id=customer_id,
    )

    if not _can_send_statement(
        request.user,
        customer,
    ):
        if (
            request.headers.get(
                "X-Requested-With"
            )
            == "XMLHttpRequest"
        ):
            return JsonResponse(
                {
                    "ok": False,
                    "errors": [
                        "No tiene permisos para enviar "
                        "estados de cuenta de este cliente."
                    ],
                },
                status=403,
            )

        return HttpResponse(
            "No tiene permisos para enviar "
            "estados de cuenta de este cliente.",
            status=403,
        )

    try:
        statement = (
            CustomerStatementPreviewService
            .create_preview(
                customer=customer,
                user=request.user,
                raw_to_emails=(
                    request.POST.get(
                        "to_emails",
                        "",
                    )
                ),
                raw_cc_emails=(
                    request.POST.get(
                        "cc_emails",
                        "",
                    )
                ),
                selected_keys=(
                    request.POST.getlist(
                        "selected_keys"
                    )
                ),
                subject=(
                    request.POST.get(
                        "subject",
                        "",
                    )
                ),
                message_body=(
                    request.POST.get(
                        "message_body",
                        "",
                    )
                ),
            )
        )

    except ValidationError as exc:

        if (
            request.headers.get(
                "X-Requested-With"
            )
            == "XMLHttpRequest"
        ):
            return JsonResponse(
                {
                    "ok": False,
                    "errors": list(
                        exc.messages
                    ),
                },
                status=400,
            )

        messages.error(
            request,
            "; ".join(exc.messages),
        )

        return redirect(
            "portfolio:customer_detail",
            customer_id=customer.id,
        )

    preview_url = reverse(
        "portfolio:customer_statement_preview",
        kwargs={
            "public_id": statement.public_id,
        },
    )

    if (
        request.headers.get(
            "X-Requested-With"
        )
        == "XMLHttpRequest"
    ):
        return JsonResponse(
            {
                "ok": True,
                "preview_url": preview_url,
            }
        )

    return redirect(
        preview_url
    )


@login_required
def customer_statement_preview(
    request,
    public_id,
):

    statement = _get_statement_for_user(
        public_id=public_id,
        user=request.user,
    )

    if (
        statement.status
        == CustomerStatement.Status.PREVIEW
        and statement.expires_at
        and statement.expires_at
        < timezone.now()
    ):
        statement.status = (
            CustomerStatement.Status.INVALIDATED
        )
        statement.error_message = (
            "La vista previa expiró antes del envío."
        )
        statement.save(
            update_fields=[
                "status",
                "error_message",
                "updated_at",
            ]
        )

    preview_snapshot = dict(
        statement.snapshot or {}
    )

    preview_documents = []

    for document in preview_snapshot.get(
        "documents",
        [],
    ):
        row = dict(document)

        try:
            original_amount = Decimal(
                str(
                    row.get(
                        "original_amount",
                        0,
                    )
                )
            )
        except Exception:
            original_amount = Decimal("0")

        try:
            balance_amount = Decimal(
                str(
                    row.get(
                        "balance_amount",
                        0,
                    )
                )
            )
        except Exception:
            balance_amount = Decimal("0")

        row["original_amount_display"] = (
            "$ "
            + f"{original_amount:,.0f}".replace(
                ",",
                ".",
            )
        )

        row["balance_amount_display"] = (
            "$ "
            + f"{balance_amount:,.0f}".replace(
                ",",
                ".",
            )
        )

        preview_documents.append(row)

    preview_snapshot[
        "documents"
    ] = preview_documents

    return render(
        request,
        "portfolio/statements/preview.html",
        {
            "statement": statement,
            "snapshot": preview_snapshot,
            "can_send": (
                statement.status
                == CustomerStatement.Status.PREVIEW
                and statement.created_by_id
                == request.user.id
                and DelegatedTokenCacheService
                .encryption_enabled()
                and request.session.get(
                    "identity_provider"
                )
                == "microsoft_entra_id"
            ),
        },
    )


@login_required
def customer_statement_pdf(
    request,
    public_id,
):

    statement = _get_statement_for_user(
        public_id=public_id,
        user=request.user,
    )

    content = (
        CustomerStatementExportService
        .build_pdf(statement.snapshot)
    )

    response = HttpResponse(
        content,
        content_type="application/pdf",
    )

    response[
        "Content-Disposition"
    ] = (
        f'attachment; filename="'
        f'{statement.pdf_filename}"'
    )

    return response


@login_required
def customer_statement_xlsx(
    request,
    public_id,
):

    statement = _get_statement_for_user(
        public_id=public_id,
        user=request.user,
    )

    content = (
        CustomerStatementExportService
        .build_xlsx(statement.snapshot)
    )

    response = HttpResponse(
        content,
        content_type=(
            "application/vnd.openxmlformats-"
            "officedocument.spreadsheetml.sheet"
        ),
    )

    response[
        "Content-Disposition"
    ] = (
        f'attachment; filename="'
        f'{statement.xlsx_filename}"'
    )

    return response

@login_required
def customer_statement_send(
    request,
    public_id,
):

    if request.method != "POST":
        return redirect(
            "portfolio:customer_statement_preview",
            public_id=public_id,
        )

    statement = _get_statement_for_user(
        public_id=public_id,
        user=request.user,
    )

    if (
        statement.created_by_id
        != request.user.id
    ):
        return HttpResponse(
            "Sólo el usuario que creó la vista previa "
            "puede realizar el envío.",
            status=403,
        )

    try:
        result = (
            CustomerStatementDeliveryService
            .deliver(
                statement_id=statement.id,
                request=request,
            )
        )

    except StatementFinancialStateChangedError as exc:
        messages.error(
            request,
            str(exc),
        )

    except StatementNotSendableError as exc:
        messages.warning(
            request,
            str(exc),
        )

    except StatementSenderMismatchError as exc:
        messages.error(
            request,
            str(exc),
        )

    except (
        DelegatedGraphUnavailableError,
        DelegatedGraphAuthenticationError,
    ) as exc:
        messages.error(
            request,
            (
                "No fue posible utilizar la sesión "
                "corporativa para enviar el correo. "
                f"{exc}"
            ),
        )

    except (
        DelegatedGraphProviderError,
        StatementDeliveryError,
    ) as exc:
        messages.error(
            request,
            (
                "El Estado de Cuenta no pudo enviarse. "
                f"{exc}"
            ),
        )

    except Exception:
        messages.error(
            request,
            "Ocurrió un error inesperado durante el envío.",
        )

    else:
        messages.success(
            request,
            "Estado de Cuenta enviado correctamente.",
        )

    return redirect(
        "portfolio:customer_statement_preview",
        public_id=public_id,
    )

