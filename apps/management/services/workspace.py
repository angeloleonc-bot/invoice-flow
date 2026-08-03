from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import (
    Case,
    Count,
    DecimalField,
    Max,
    Q,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.management.models import (
    CollectionAction,
    OperationalAlert,
    PaymentPromise,
)
from apps.portfolio.models import (
    Document,
    DocumentAssignment,
)


class WorkspacePortfolioService:
    """
    Servicios específicos del Workspace Operacional.

    Este servicio todavía no representa el futuro
    OperationalPortfolioService global.

    Su objetivo es separar:

    - definición de cartera visible;
    - KPIs;
    - resumen financiero de la página;
    - enriquecimiento operacional de clientes.
    """

    ACTIVE_ALERT_STATUSES = [
        OperationalAlert.AlertStatus.NEW,
        OperationalAlert.AlertStatus.VIEWED,
        OperationalAlert.AlertStatus.IN_PROGRESS,
        OperationalAlert.AlertStatus.REOPENED,
    ]

    OPEN_PROMISE_STATUSES = [
        PaymentPromise.Status.ACTIVE,
        PaymentPromise.Status.PENDING,
        PaymentPromise.Status.EXPIRED,
    ]

    def __init__(
        self,
        *,
        user,
        selected_scope: str,
        selected_collector_user=None,
    ):
        self.user = user
        self.selected_scope = selected_scope
        self.selected_collector_user = selected_collector_user
        self.today = timezone.localdate()
        self.now = timezone.now()

        self.zero_decimal = Value(
            Decimal("0.00"),
            output_field=DecimalField(
                max_digits=18,
                decimal_places=2,
            ),
        )

    def visible_documents(self):
        """
        Definición única de documentos visibles en el Workspace.

        Respeta exactamente el alcance actual:

        - scope=my: documentos asignados al usuario;
        - scope=all sin cobrador: toda la cartera;
        - scope=all con cobrador: cartera del cobrador seleccionado.
        """

        queryset = Document.objects.filter(
            assignments__is_active=True,
        )

        if self.selected_scope == "my":
            queryset = queryset.filter(
                assignments__assigned_to=self.user,
            )

        elif self.selected_collector_user is not None:
            queryset = queryset.filter(
                assignments__assigned_to=self.selected_collector_user,
            )

        return queryset

    def visible_open_documents(self):
        return self.visible_documents().filter(
            balance_amount__gt=0,
        )

    def visible_portfolio_documents(self):
        """
        Documentos que permiten que un cliente aparezca en el Workspace.

        Conserva la regla actual:

        - saldo pendiente mayor que cero; o
        - saldo a favor mayor que cero.
        """

        return self.visible_documents().filter(
            Q(balance_amount__gt=0)
            | Q(overpayment_amount__gt=0)
        )

    def visible_customer_ids(self):
        return (
            self.visible_portfolio_documents()
            .values("customer_id")
            .distinct()
        )

    def scoped_actions(self):
        queryset = CollectionAction.objects.filter(
            document__assignments__is_active=True,
        )

        if self.selected_scope == "my":
            queryset = queryset.filter(
                document__assignments__assigned_to=self.user,
            )

        elif self.selected_collector_user is not None:
            queryset = queryset.filter(
                document__assignments__assigned_to=(
                    self.selected_collector_user
                ),
            )

        return queryset

    def active_alerts(self):
        return OperationalAlert.objects.filter(
            Q(status__in=self.ACTIVE_ALERT_STATUSES)
            | Q(
                status=OperationalAlert.AlertStatus.POSTPONED,
                due_at__lte=self.now,
            )
        )

    def get_kpis(self):
        """
        Calcula los KPIs sin depender del queryset del listado.

        Cada consulta se realiza sobre la entidad adecuada.
        """

        portfolio_documents = self.visible_portfolio_documents()
        open_documents = self.visible_open_documents()

        active_customer_ids = set(
            portfolio_documents.values_list(
                "customer_id",
                flat=True,
            ).distinct()
        )

        financial_totals = open_documents.aggregate(
            pending_balance=Coalesce(
                Sum("balance_amount"),
                self.zero_decimal,
            ),
        )

        if not active_customer_ids:
            return {
                "active_customers": 0,
                "pending_balance": Decimal("0.00"),
                "attention_customers": 0,
                "expired_promises": 0,
            }

        expired_promise_customer_ids = set(
            PaymentPromise.objects.filter(
                customer_id__in=active_customer_ids,
                status=PaymentPromise.Status.EXPIRED,
            )
            .values_list("customer_id", flat=True)
            .distinct()
        )

        active_alert_customer_ids = set(
            self.active_alerts()
            .filter(
                customer_id__in=active_customer_ids,
            )
            .exclude(customer_id__isnull=True)
            .values_list("customer_id", flat=True)
            .distinct()
        )

        managed_customer_ids = set(
            self.scoped_actions()
            .filter(
                customer_id__in=active_customer_ids,
            )
            .values_list("customer_id", flat=True)
            .distinct()
        )

        customers_without_management = (
            active_customer_ids - managed_customer_ids
        )

        attention_customer_ids = (
            expired_promise_customer_ids
            | active_alert_customer_ids
            | customers_without_management
        )

        return {
            "active_customers": len(active_customer_ids),
            "pending_balance": (
                financial_totals["pending_balance"]
                or Decimal("0.00")
            ),
            "attention_customers": len(attention_customer_ids),
            "expired_promises": len(
                expired_promise_customer_ids
            ),
        }

    def get_page_financial_summary(self, customer_ids):
        """
        Calcula aging completo solamente para los clientes de la página.

        De esta manera se conservan todas las columnas actuales sin obligar
        a SQL Server a calcular todos los tramos para toda la cartera.
        """

        customer_ids = list(customer_ids)

        if not customer_ids:
            return {}

        today = self.today
        open_documents = self.visible_open_documents().filter(
            customer_id__in=customer_ids,
        )

        rows = (
            open_documents
            .values("customer_id")
            .annotate(
                total_balance=Coalesce(
                    Sum("balance_amount"),
                    self.zero_decimal,
                ),
                current_balance=Coalesce(
                    Sum(
                        "balance_amount",
                        filter=Q(due_date__gte=today),
                    ),
                    self.zero_decimal,
                ),
                aging_0_15=Coalesce(
                    Sum(
                        "balance_amount",
                        filter=Q(
                            due_date__lt=today,
                            due_date__gte=(
                                today - timedelta(days=15)
                            ),
                        ),
                    ),
                    self.zero_decimal,
                ),
                aging_16_30=Coalesce(
                    Sum(
                        "balance_amount",
                        filter=Q(
                            due_date__lt=(
                                today - timedelta(days=15)
                            ),
                            due_date__gte=(
                                today - timedelta(days=30)
                            ),
                        ),
                    ),
                    self.zero_decimal,
                ),
                aging_31_45=Coalesce(
                    Sum(
                        "balance_amount",
                        filter=Q(
                            due_date__lt=(
                                today - timedelta(days=30)
                            ),
                            due_date__gte=(
                                today - timedelta(days=45)
                            ),
                        ),
                    ),
                    self.zero_decimal,
                ),
                aging_46_60=Coalesce(
                    Sum(
                        "balance_amount",
                        filter=Q(
                            due_date__lt=(
                                today - timedelta(days=45)
                            ),
                            due_date__gte=(
                                today - timedelta(days=60)
                            ),
                        ),
                    ),
                    self.zero_decimal,
                ),
                aging_61_90=Coalesce(
                    Sum(
                        "balance_amount",
                        filter=Q(
                            due_date__lt=(
                                today - timedelta(days=60)
                            ),
                            due_date__gte=(
                                today - timedelta(days=90)
                            ),
                        ),
                    ),
                    self.zero_decimal,
                ),
                aging_91_120=Coalesce(
                    Sum(
                        "balance_amount",
                        filter=Q(
                            due_date__lt=(
                                today - timedelta(days=90)
                            ),
                            due_date__gte=(
                                today - timedelta(days=120)
                            ),
                        ),
                    ),
                    self.zero_decimal,
                ),
                aging_121_plus=Coalesce(
                    Sum(
                        "balance_amount",
                        filter=Q(
                            due_date__lt=(
                                today - timedelta(days=120)
                            ),
                        ),
                    ),
                    self.zero_decimal,
                ),
                open_documents_count=Count(
                    "id",
                    distinct=True,
                ),
            )
        )

        summary = {
            row["customer_id"]: row
            for row in rows
        }

        overpayment_rows = (
            self.visible_documents()
            .filter(
                customer_id__in=customer_ids,
            )
            .values("customer_id")
            .annotate(
                total_overpayment=Coalesce(
                    Sum("overpayment_amount"),
                    self.zero_decimal,
                )
            )
        )

        for row in overpayment_rows:
            customer_id = row["customer_id"]

            summary.setdefault(
                customer_id,
                {},
            )["total_overpayment"] = (
                row["total_overpayment"]
                or Decimal("0.00")
            )

        return summary

    def get_collectors_by_customer(self, customer_ids):
        customer_ids = list(customer_ids)

        if not customer_ids:
            return {}

        assignments = (
            DocumentAssignment.objects
            .filter(
                is_active=True,
                document__customer_id__in=customer_ids,
            )
            .values(
                "document__customer_id",
                "assigned_to_id",
                "assigned_to__first_name",
                "assigned_to__last_name",
                "assigned_to__username",
            )
            .distinct()
            .order_by(
                "assigned_to__first_name",
                "assigned_to__last_name",
                "assigned_to__username",
            )
        )

        if self.selected_scope == "my":
            assignments = assignments.filter(
                assigned_to=self.user,
            )

        elif self.selected_collector_user is not None:
            assignments = assignments.filter(
                assigned_to=self.selected_collector_user,
            )

        result = defaultdict(list)

        for assignment in assignments:
            full_name = " ".join(
                part
                for part in [
                    assignment["assigned_to__first_name"],
                    assignment["assigned_to__last_name"],
                ]
                if part
            ).strip()

            collector_name = (
                full_name
                or assignment["assigned_to__username"]
            )

            result[
                assignment["document__customer_id"]
            ].append(collector_name)

        return dict(result)

    def get_last_actions_by_customer(self, customer_ids):
        customer_ids = list(customer_ids)

        if not customer_ids:
            return {}

        actions = (
            self.scoped_actions()
            .filter(customer_id__in=customer_ids)
            .values(
                "customer_id",
                "action_date",
                "title",
                "action_type",
                "created_at",
            )
            .order_by(
                "customer_id",
                "-action_date",
                "-created_at",
            )
        )

        result = {}

        for action in actions:
            customer_id = action["customer_id"]

            if customer_id not in result:
                result[customer_id] = action

        return result

    def get_relevant_promises_by_customer(self, customer_ids):
        customer_ids = list(customer_ids)

        if not customer_ids:
            return {}

        promises = (
            PaymentPromise.objects
            .filter(
                customer_id__in=customer_ids,
                status__in=self.OPEN_PROMISE_STATUSES,
            )
            .annotate(
                operational_order=Case(
                    When(
                        status=PaymentPromise.Status.EXPIRED,
                        then=Value(0),
                    ),
                    When(
                        status=PaymentPromise.Status.ACTIVE,
                        then=Value(1),
                    ),
                    When(
                        status=PaymentPromise.Status.PENDING,
                        then=Value(2),
                    ),
                    default=Value(3),
                )
            )
            .values(
                "customer_id",
                "promise_date",
                "promised_amount",
                "status",
                "created_at",
                "operational_order",
            )
            .order_by(
                "customer_id",
                "operational_order",
                "promise_date",
                "-created_at",
            )
        )

        result = {}

        for promise in promises:
            customer_id = promise["customer_id"]

            if customer_id not in result:
                result[customer_id] = promise

        return result

    def get_alert_flags_by_customer(self, customer_ids):
        customer_ids = list(customer_ids)

        if not customer_ids:
            return {}

        rows = (
            self.active_alerts()
            .filter(
                customer_id__in=customer_ids,
            )
            .exclude(customer_id__isnull=True)
            .values("customer_id")
            .annotate(
                has_active_alert=Count("id"),
                critical_count=Count(
                    "id",
                    filter=Q(
                        alert_type=(
                            OperationalAlert
                            .AlertType
                            .CRITICAL_CUSTOMER
                        )
                    ),
                ),
            )
        )

        return {
            row["customer_id"]: {
                "has_active_alert": (
                    row["has_active_alert"] > 0
                ),
                "is_critical": (
                    row["critical_count"] > 0
                ),
            }
            for row in rows
        }

    def get_promise_flags_by_customer(self, customer_ids):
        customer_ids = list(customer_ids)

        if not customer_ids:
            return {}

        rows = (
            PaymentPromise.objects
            .filter(
                customer_id__in=customer_ids,
                status__in=self.OPEN_PROMISE_STATUSES,
            )
            .values("customer_id")
            .annotate(
                expired_count=Count(
                    "id",
                    filter=Q(
                        status=PaymentPromise.Status.EXPIRED,
                    ),
                ),
                active_count=Count(
                    "id",
                    filter=Q(
                        status__in=[
                            PaymentPromise.Status.ACTIVE,
                            PaymentPromise.Status.PENDING,
                        ],
                    ),
                ),
            )
        )

        return {
            row["customer_id"]: {
                "has_expired_promise": (
                    row["expired_count"] > 0
                ),
                "has_active_promise": (
                    row["active_count"] > 0
                ),
            }
            for row in rows
        }