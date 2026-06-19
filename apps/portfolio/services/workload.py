from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.management.models import PaymentPromise
from apps.portfolio.models import DocumentAssignment


class WorkloadService:
    DOCUMENTS_WEIGHT = Decimal("0.40")
    BALANCE_WEIGHT = Decimal("0.30")
    CRITICAL_WEIGHT = Decimal("0.20")
    OVERDUE_PROMISES_WEIGHT = Decimal("0.10")

    CRITICAL_OVERDUE_DAYS = 30

    def __init__(self):
        self.today = timezone.localdate()

    def get_workloads(self):
        User = get_user_model()

        collectors = (
            User.objects.filter(portfolio_assignments_received__is_active=True)
            .annotate(
                active_documents=Count(
                    "portfolio_assignments_received__document",
                    filter=Q(portfolio_assignments_received__is_active=True),
                    distinct=True,
                ),
                assigned_balance=Coalesce(
                    Sum(
                        "portfolio_assignments_received__document__balance_amount",
                        filter=Q(portfolio_assignments_received__is_active=True),
                    ),
                    Decimal("0"),
                ),
                critical_documents=Count(
                    "portfolio_assignments_received__document",
                    filter=Q(
                        portfolio_assignments_received__is_active=True,
                        portfolio_assignments_received__document__due_date__lt=(
                            self.today - timezone.timedelta(days=self.CRITICAL_OVERDUE_DAYS)
                        ),
                    ),
                    distinct=True,
                ),
                overdue_promises=Count(
                    "portfolio_assignments_received__document__promise_documents__promise",
                    filter=Q(
                        portfolio_assignments_received__is_active=True,
                        portfolio_assignments_received__document__promise_documents__promise__promise_date__lt=self.today,
                        portfolio_assignments_received__document__promise_documents__promise__payment_confirmed=False,
                        portfolio_assignments_received__document__promise_documents__promise__status__in=[
                            PaymentPromise.Status.PENDING,
                            PaymentPromise.Status.ACTIVE,
                            PaymentPromise.Status.EXPIRED,
                        ],
                    ),
                    distinct=True,
                ),
            )
            .order_by("username")
        )

        max_documents = max([collector.active_documents for collector in collectors] or [1])
        max_balance = max([collector.assigned_balance or Decimal("0") for collector in collectors] or [Decimal("1")])
        max_critical = max([collector.critical_documents for collector in collectors] or [1])
        max_overdue_promises = max([collector.overdue_promises for collector in collectors] or [1])

        max_documents = max_documents or 1
        max_balance = max_balance or Decimal("1")
        max_critical = max_critical or 1
        max_overdue_promises = max_overdue_promises or 1

        workloads = []

        for collector in collectors:
            documents_score = Decimal(collector.active_documents or 0) / Decimal(max_documents)
            balance_score = Decimal(collector.assigned_balance or 0) / Decimal(max_balance)
            critical_score = Decimal(collector.critical_documents or 0) / Decimal(max_critical)
            promises_score = Decimal(collector.overdue_promises or 0) / Decimal(max_overdue_promises)

            workload_score = (
                documents_score * self.DOCUMENTS_WEIGHT
                + balance_score * self.BALANCE_WEIGHT
                + critical_score * self.CRITICAL_WEIGHT
                + promises_score * self.OVERDUE_PROMISES_WEIGHT
            )

            workload_percentage = int(round(workload_score * 100, 0))

            workloads.append(
                {
                    "collector": collector,
                    "collector_name": collector.get_full_name() or collector.username,
                    "active_documents": collector.active_documents or 0,
                    "assigned_balance": collector.assigned_balance or Decimal("0"),
                    "critical_documents": collector.critical_documents or 0,
                    "overdue_promises": collector.overdue_promises or 0,
                    "workload_percentage": workload_percentage,
                    "workload_score": workload_score,
                }
            )

        return sorted(workloads, key=lambda item: item["workload_percentage"], reverse=True)

    def get_ranking(self, limit=None):
        ranking = self.get_workloads()
        if limit:
            return ranking[:limit]
        return ranking


class WorkloadRecommendationService:
    def __init__(self):
        self.workload_service = WorkloadService()

    def recommend_collector(self):
        workloads = self.workload_service.get_workloads()

        if not workloads:
            return None

        ranked = sorted(
            workloads,
            key=lambda item: (
                item["workload_percentage"],
                item["assigned_balance"],
                item["critical_documents"],
                item["active_documents"],
            ),
        )

        recommended = ranked[0]

        return {
            "collector": recommended["collector"],
            "collector_name": recommended["collector_name"],
            "workload_percentage": recommended["workload_percentage"],
            "assigned_balance": recommended["assigned_balance"],
            "active_documents": recommended["active_documents"],
            "critical_documents": recommended["critical_documents"],
            "reasons": [
                "Menor carga operacional",
                "Menor saldo asignado",
                "Menor cartera crítica",
                "Capacidad disponible",
            ],
        }
