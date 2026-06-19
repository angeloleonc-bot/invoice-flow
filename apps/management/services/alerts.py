from datetime import timedelta

from django.db.models import Max, Q
from django.utils import timezone

from apps.management.models import CollectionAction, OperationalAlert, PaymentPromise
from apps.management.services.prioritization import WorklistPriorityService
from apps.portfolio.models import Document, DocumentAssignment


class OperationalAlertService:
    ACTIVE_STATUSES = [
        OperationalAlert.AlertStatus.NEW,
        OperationalAlert.AlertStatus.VIEWED,
        OperationalAlert.AlertStatus.IN_PROGRESS,
        OperationalAlert.AlertStatus.POSTPONED,
    ]

    FINAL_STATUSES = [
        OperationalAlert.AlertStatus.RESOLVED,
        OperationalAlert.AlertStatus.DISMISSED,
    ]

    @classmethod
    def create_alert(
        cls,
        *,
        alert_type,
        severity,
        title,
        message,
        customer=None,
        document=None,
        promise=None,
        assigned_to=None,
        due_at=None,
        metadata=None,
    ):
        existing_alert = OperationalAlert.objects.filter(
            alert_type=alert_type,
            customer=customer,
            document=document,
            promise=promise,
        ).order_by("-created_at").first()

        if existing_alert:
            return existing_alert, False

        alert = OperationalAlert.objects.create(
            alert_type=alert_type,
            severity=severity,
            title=title,
            message=message,
            customer=customer,
            document=document,
            promise=promise,
            assigned_to=assigned_to,
            due_at=due_at,
            metadata=metadata or {},
        )
        return alert, True

    @classmethod
    def get_active_alerts(cls):
        return OperationalAlert.objects.filter(
            status__in=cls.ACTIVE_STATUSES,
        ).select_related(
            "customer",
            "document",
            "promise",
            "assigned_to",
        )

    @classmethod
    def mark_viewed(cls, alert):
        if alert.status == OperationalAlert.AlertStatus.NEW:
            alert.status = OperationalAlert.AlertStatus.VIEWED
            alert.save(update_fields=["status"])
        return alert

    @classmethod
    def mark_resolved(cls, alert):
        alert.status = OperationalAlert.AlertStatus.RESOLVED
        alert.resolved_at = timezone.now()
        alert.save(update_fields=["status", "resolved_at"])
        return alert

    @classmethod
    def mark_postponed(cls, alert, days=1):
        alert.status = OperationalAlert.AlertStatus.POSTPONED
        alert.due_at = timezone.now() + timedelta(days=days)
        alert.save(update_fields=["status", "due_at"])
        return alert

    @classmethod
    def mark_dismissed(cls, alert):
        alert.status = OperationalAlert.AlertStatus.DISMISSED
        alert.resolved_at = timezone.now()
        alert.save(update_fields=["status", "resolved_at"])
        return alert

    @classmethod
    def generate_alerts(cls):
        created = {
            "promise_expired": 0,
            "promise_due_today": 0,
            "no_management_7_days": 0,
            "high_priority_document": 0,
            "unassigned_document": 0,
        }

        created["promise_expired"] = cls.generate_promise_expired_alerts()
        created["promise_due_today"] = cls.generate_promise_due_today_alerts()
        created["no_management_7_days"] = cls.generate_no_management_7_days_alerts()
        created["high_priority_document"] = cls.generate_high_priority_document_alerts()
        created["unassigned_document"] = cls.generate_unassigned_document_alerts()

        return created

    @classmethod
    def generate_promise_expired_alerts(cls):
        today = timezone.localdate()
        count = 0

        promises = PaymentPromise.objects.filter(
            status__in=["ACTIVE", "PENDING"],
            promise_date__lt=today,
        ).select_related("customer")

        for promise in promises:
            document = promise.documents.first() if hasattr(promise, "documents") else None

            _, created = cls.create_alert(
                alert_type=OperationalAlert.AlertType.PROMISE_EXPIRED,
                severity=OperationalAlert.Severity.HIGH,
                title="Promesa de pago vencida",
                message=f"La promesa de pago del cliente {promise.customer} venció el {promise.promise_date}.",
                customer=promise.customer,
                document=document,
                promise=promise,
                due_at=timezone.now(),
                metadata={"promise_date": str(promise.promise_date)},
            )
            if created:
                count += 1

        return count

    @classmethod
    def generate_promise_due_today_alerts(cls):
        today = timezone.localdate()
        count = 0

        promises = PaymentPromise.objects.filter(
            status__in=["ACTIVE", "PENDING"],
            promise_date=today,
        ).select_related("customer")

        for promise in promises:
            document = promise.documents.first() if hasattr(promise, "documents") else None

            _, created = cls.create_alert(
                alert_type=OperationalAlert.AlertType.PROMISE_DUE_TODAY,
                severity=OperationalAlert.Severity.MEDIUM,
                title="Promesa de pago vence hoy",
                message=f"La promesa de pago del cliente {promise.customer} vence hoy.",
                customer=promise.customer,
                document=document,
                promise=promise,
                due_at=timezone.now(),
                metadata={"promise_date": str(promise.promise_date)},
            )
            if created:
                count += 1

        return count

    @classmethod
    def generate_no_management_7_days_alerts(cls):
        limit_date = timezone.now() - timedelta(days=7)
        count = 0

        managed_document_ids = CollectionAction.objects.filter(
            action_date__gte=limit_date,
        ).values_list("document_id", flat=True)

        documents = Document.objects.exclude(
            id__in=managed_document_ids
        ).select_related("customer")

        for document in documents:
            _, created = cls.create_alert(
                alert_type=OperationalAlert.AlertType.NO_MANAGEMENT_7_DAYS,
                severity=OperationalAlert.Severity.MEDIUM,
                title="Documento sin gestión reciente",
                message=f"El documento {document} no registra gestión en los últimos 7 días.",
                customer=document.customer,
                document=document,
                assigned_to=getattr(document, "assigned_collector", None),
                due_at=timezone.now(),
                metadata={
                    "reason": "Sin acciones de gestión en los últimos 7 días"
                },
            )
            if created:
                count += 1

        return count

    @classmethod
    def generate_high_priority_document_alerts(cls):
        count = 0
        priority_service = WorklistPriorityService()

        documents = Document.objects.select_related("customer")

        for document in documents:
            priority_data = priority_service.evaluate_document(document)
            priority_score = priority_data.get("priority_score", 0)

            if priority_score >= 100:
                _, created = cls.create_alert(
                    alert_type=OperationalAlert.AlertType.HIGH_PRIORITY_DOCUMENT,
                    severity=OperationalAlert.Severity.HIGH,
                    title="Documento de alta prioridad",
                    message=f"El documento {document} alcanzó prioridad operacional alta.",
                    customer=document.customer,
                    document=document,
                    assigned_to=getattr(document, "assigned_collector", None),
                    due_at=timezone.now(),
                    metadata={
                        "priority_score": priority_score,
                        "priority_reasons": priority_data.get("priority_reasons", []),
                        "priority_reason_codes": priority_data.get("priority_reason_codes", []),
                    },
                )
                if created:
                    count += 1

        return count

    @classmethod
    def generate_unassigned_document_alerts(cls):
        count = 0

        assigned_document_ids = DocumentAssignment.objects.filter(
            is_active=True
        ).values_list("document_id", flat=True)

        documents = Document.objects.exclude(
            id__in=assigned_document_ids
        ).select_related("customer")

        for document in documents:
            _, created = cls.create_alert(
                alert_type=OperationalAlert.AlertType.UNASSIGNED_DOCUMENT,
                severity=OperationalAlert.Severity.HIGH,
                title="Documento sin asignar",
                message=f"El documento {document} no tiene cobrador activo asignado.",
                customer=document.customer,
                document=document,
                due_at=timezone.now(),
                metadata={},
            )
            if created:
                count += 1

        return count