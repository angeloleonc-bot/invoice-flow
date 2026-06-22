from datetime import timedelta
from decimal import Decimal

from django.db.models import Case, Count, Max, Q, Sum, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.management.models import CollectionAction, PaymentPromise
from apps.management.services.prioritization import WorklistPriorityService
from apps.portfolio.models import Document, DocumentAssignment, PaymentRecord

from apps.portfolio.services.workload import WorkloadService

from apps.management.models import OperationalAlert
from apps.management.services.alerts import OperationalAlertService

class DashboardService:
    """
    Servicio del Dashboard Supervisor Operacional.

    Centraliza métricas supervisoras sin ensuciar la vista:
    - KPIs superiores
    - rendimiento de cobradores
    - documentos críticos
    - próximos compromisos
    - aging
    - alertas operacionales
    """

    HIGH_PRIORITY_MIN_SCORE = 1

    def __init__(self):
        self.today = timezone.localdate()
        self.now = timezone.now()
        self.month_start = self.today.replace(day=1)
        self.priority_service = WorklistPriorityService()

    def get_context(self, user=None):
        critical_documents = self.get_critical_documents(limit=10)
        aging = self.get_aging()
        total_aging_documents = sum(bucket["count"] for bucket in aging) or 1
        unattended_portfolio = self.get_unattended_portfolio()

        operational_alerts = OperationalAlertService.get_visible_active_alerts(user)

        operational_alert_summary = {
            "new": operational_alerts.filter(
                status=OperationalAlert.AlertStatus.NEW
            ).count(),
            "critical": operational_alerts.filter(
                severity=OperationalAlert.Severity.CRITICAL
            ).count(),
            "high": operational_alerts.filter(
                severity=OperationalAlert.Severity.HIGH
            ).count(),
            "aged": operational_alerts.filter(
                created_at__lt=self.now - timedelta(days=7)
            ).count(),
            "reopened": operational_alerts.filter(
                status=OperationalAlert.AlertStatus.REOPENED
            ).count(),
            "postponed": operational_alerts.filter(
                status=OperationalAlert.AlertStatus.POSTPONED
            ).count(),
        }

        recent_operational_alerts = operational_alerts.order_by("-created_at")[:5]

        return {
            "kpis": self.get_kpis(),
            "team_performance": self.get_team_performance(),
            "team_workload": self.get_team_workload(),
            "critical_documents": critical_documents,
            "commitments": self.get_commitments(),
            "aging": aging,
            "total_aging_documents": total_aging_documents,
            "alerts": self.get_alerts(),
            "operational_alert_summary": operational_alert_summary,
            "recent_operational_alerts": recent_operational_alerts,
            "operational_alert_aging": OperationalAlertService.aging_summary_for_user(user),
            "operational_alerts_by_responsible": OperationalAlertService.alerts_by_responsible(user),
            "critical_customer_summary": OperationalAlertService.critical_customer_summary_for_user(user),
            "unattended_portfolio": unattended_portfolio,
        }

    def get_kpis(self):
        total_receivable = Document.objects.aggregate(
            total=Coalesce(Sum("balance_amount"), Decimal("0"))
        )["total"]

        recovered_month = PaymentRecord.objects.filter(
            payment_date__gte=self.month_start,
            payment_date__lte=self.today,
        ).aggregate(
            total=Coalesce(Sum("amount"), Decimal("0"))
        )["total"]

        overdue_promises = PaymentPromise.objects.filter(
            promise_date__lt=self.today,
            payment_confirmed=False,
            status__in=[
                PaymentPromise.Status.PENDING,
                PaymentPromise.Status.ACTIVE,
                PaymentPromise.Status.EXPIRED,
            ],
        ).count()

        high_priority_documents = sum(
            1
            for item in self._get_prioritized_documents()
            if item["priority_score"] >= self.HIGH_PRIORITY_MIN_SCORE
        )

        return {
            "total_receivable": total_receivable,
            "recovered_month": recovered_month,
            "overdue_promises": overdue_promises,
            "high_priority_documents": high_priority_documents,
        }

    def get_team_performance(self):
        assignments = (
            DocumentAssignment.objects
            .filter(is_active=True)
            .select_related("assigned_to", "document", "document__customer")
            .values(
                "assigned_to_id",
                "assigned_to__first_name",
                "assigned_to__last_name",
                "assigned_to__username",
            )
            .annotate(
                assigned_documents=Count("document", distinct=True),
                assigned_balance=Coalesce(Sum("document__balance_amount"), Decimal("0")),
            )
            .order_by("assigned_to__username")
        )

        prioritized_documents = self._get_prioritized_documents()
        priority_by_document_id = {
            item["document"].id: item["priority_score"]
            for item in prioritized_documents
        }

        result = []

        for row in assignments:
            assigned_to_id = row["assigned_to_id"]

            document_ids = list(
                DocumentAssignment.objects.filter(
                    assigned_to_id=assigned_to_id,
                    is_active=True,
                ).values_list("document_id", flat=True)
            )

            payments = PaymentRecord.objects.filter(
                document_id__in=document_ids,
                payment_date__gte=self.month_start,
                payment_date__lte=self.today,
            ).aggregate(
                total=Coalesce(Sum("amount"), Decimal("0"))
            )["total"]

            overdue_promises = PaymentPromise.objects.filter(
                promise_documents__document_id__in=document_ids,
                promise_date__lt=self.today,
                payment_confirmed=False,
                status__in=[
                    PaymentPromise.Status.PENDING,
                    PaymentPromise.Status.ACTIVE,
                    PaymentPromise.Status.EXPIRED,
                ],
            ).distinct().count()

            high_priority_documents = sum(
                1
                for document_id in document_ids
                if priority_by_document_id.get(document_id, 0) >= self.HIGH_PRIORITY_MIN_SCORE
            )

            full_name = (
                f"{row['assigned_to__first_name']} {row['assigned_to__last_name']}"
            ).strip()

            result.append({
                "collector_name": full_name or row["assigned_to__username"],
                "assigned_documents": row["assigned_documents"],
                "assigned_balance": row["assigned_balance"],
                "payments": payments,
                "overdue_promises": overdue_promises,
                "high_priority_documents": high_priority_documents,
            })

        return result

    def get_critical_documents(self, limit=10):
        prioritized_documents = self._get_prioritized_documents()

        sorted_documents = self.priority_service.sort_items(prioritized_documents)

        return sorted_documents[:limit]

    def get_commitments(self, limit=15):
        promises = (
            PaymentPromise.objects
            .select_related("customer")
            .prefetch_related("promise_documents__document")
            .filter(
                payment_confirmed=False,
                status__in=[
                    PaymentPromise.Status.PENDING,
                    PaymentPromise.Status.ACTIVE,
                    PaymentPromise.Status.EXPIRED,
                ],
            )
            .filter(
                Q(promise_date__lt=self.today)
                | Q(promise_date=self.today)
                | Q(promise_date__lte=self.today + timedelta(days=7))
            )
            .order_by("promise_date", "-created_at")[:limit]
        )

        commitments = []

        for promise in promises:
            if promise.promise_date < self.today:
                status_label = "Vencida"
                status_key = "overdue"
            elif promise.promise_date == self.today:
                status_label = "Hoy"
                status_key = "today"
            else:
                status_label = "Próxima"
                status_key = "upcoming"

            first_document = None
            promise_document = promise.promise_documents.first()
            if promise_document:
                first_document = promise_document.document

            commitments.append({
                "promise": promise,
                "customer": promise.customer,
                "document": first_document,
                "promise_date": promise.promise_date,
                "promised_amount": promise.promised_amount,
                "status_label": status_label,
                "status_key": status_key,
            })

        return commitments

    def get_aging(self):
        today = self.today

        buckets = [
            {
                "key": "current",
                "label": "Vigente",
                "query": Q(due_date__gte=today),
            },
            {
                "key": "days_1_30",
                "label": "1-30 días",
                "query": Q(due_date__lt=today, due_date__gte=today - timedelta(days=30)),
            },
            {
                "key": "days_31_60",
                "label": "31-60 días",
                "query": Q(due_date__lt=today - timedelta(days=30), due_date__gte=today - timedelta(days=60)),
            },
            {
                "key": "days_61_90",
                "label": "61-90 días",
                "query": Q(due_date__lt=today - timedelta(days=60), due_date__gte=today - timedelta(days=90)),
            },
            {
                "key": "days_90_plus",
                "label": "90+ días",
                "query": Q(due_date__lt=today - timedelta(days=90)),
            },
        ]

        result = []

        for bucket in buckets:
            data = Document.objects.filter(bucket["query"]).aggregate(
                count=Count("id"),
                balance=Coalesce(Sum("balance_amount"), Decimal("0")),
            )

            result.append({
                "key": bucket["key"],
                "label": bucket["label"],
                "count": data["count"],
                "balance": data["balance"],
                "url": f"/management/my-work/?aging={bucket['key']}",
            })

        return result
    
    def get_unattended_portfolio(self):
        thresholds = [
            {
                "key": "without_action_7",
                "label": "Sin gestión > 7 días",
                "days": 7,
                "description": "Documentos sin gestión operacional reciente.",
            },
            {
                "key": "without_action_14",
                "label": "Sin gestión > 14 días",
                "days": 14,
                "description": "Documentos que requieren revisión supervisora.",
            },
            {
                "key": "without_action_30",
                "label": "Sin gestión > 30 días",
                "days": 30,
                "description": "Cartera crítica sin seguimiento operativo.",
            },
        ]

        last_actions = (
            CollectionAction.objects
            .filter(document_id__isnull=False)
            .values("document_id")
            .annotate(last_action_date=Max("action_date"))
        )

        result = []

        for threshold in thresholds:
            cutoff = self.now - timedelta(days=threshold["days"])

            recent_document_ids = last_actions.filter(
                last_action_date__gte=cutoff
            ).values_list("document_id", flat=True)

            data = (
                Document.objects
                .filter(balance_amount__gt=0)
                .exclude(id__in=recent_document_ids)
                .aggregate(
                    count=Count("id"),
                    balance=Coalesce(Sum("balance_amount"), Decimal("0")),
                )
            )

            result.append({
                **threshold,
                "count": data["count"],
                "balance": data["balance"],
                "url": f"/management/my-work/?attention={threshold['days']}",
            })

        return result

    def get_alerts(self):
        seven_days_ago = self.now - timedelta(days=7)

        unassigned_documents = Document.objects.filter(
            assignments__isnull=True
        ).count()

        overdue_promises = PaymentPromise.objects.filter(
            promise_date__lt=self.today,
            payment_confirmed=False,
            status__in=[
                PaymentPromise.Status.PENDING,
                PaymentPromise.Status.ACTIVE,
                PaymentPromise.Status.EXPIRED,
            ],
        ).count()

        last_action_by_document = (
            CollectionAction.objects
            .values("document_id")
            .annotate(last_action=Max("action_date"))
            .filter(last_action__gte=seven_days_ago)
            .values_list("document_id", flat=True)
        )

        without_management_7_days = Document.objects.exclude(
            id__in=last_action_by_document
        ).count()

        high_priority_documents = sum(
            1
            for item in self._get_prioritized_documents()
            if item["priority_score"] >= self.HIGH_PRIORITY_MIN_SCORE
        )

        return [
            {
                "label": "Documentos sin asignar",
                "value": unassigned_documents,
                "description": "Documentos sin cobrador activo asignado.",
                "severity": "warning" if unassigned_documents else "ok",
            },
            {
                "label": "Promesas vencidas",
                "value": overdue_promises,
                "description": "Compromisos vencidos sin confirmación de pago.",
                "severity": "danger" if overdue_promises else "ok",
            },
            {
                "label": "Sin gestión 7 días",
                "value": without_management_7_days,
                "description": "Documentos sin acciones recientes de cobranza.",
                "severity": "warning" if without_management_7_days else "ok",
            },
            {
                "label": "Alta prioridad",
                "value": high_priority_documents,
                "description": "Documentos con reglas de prioridad activas.",
                "severity": "danger" if high_priority_documents else "ok",
            },
        ]

    def _get_prioritized_documents(self):
        documents = (
            Document.objects
            .filter(balance_amount__gt=0)
            .select_related("customer", "status", "sub_status")
            .prefetch_related("tags", "assignments")
        )

        prioritized = []

        for document in documents:
            priority_data = self.priority_service.evaluate_document(document)

            days_overdue = 0
            if document.due_date and document.due_date < self.today:
                days_overdue = (self.today - document.due_date).days

            prioritized.append({
                "document": document,
                "customer": document.customer,
                "priority_score": priority_data["priority_score"],
                "priority_reason": priority_data["main_priority_reason"],
                "priority_reasons": priority_data["priority_reasons"],
                "days_overdue": days_overdue,
            })

        return prioritized
    
    def get_team_workload(self):
        return WorkloadService().get_ranking(limit=5)