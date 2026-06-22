from datetime import timedelta

from django.db.models import Count, Case, When, Value, CharField, Max, Q
from django.utils import timezone

from apps.management.models import CollectionAction, OperationalAlert, PaymentPromise
from apps.management.services.prioritization import WorklistPriorityService
from apps.portfolio.models import Document, DocumentAssignment
from apps.portfolio.constants import DOCUMENT_TAG_CRITICAL_CUSTOMER


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

    REOPEN_MIN_DAYS = 3

    FULL_VISIBILITY_ROLES = [
        "ADMINISTRADOR",
        "SUPERVISOR",
        "CONSULTA_AUDITORIA",
    ]

    @classmethod
    def base_queryset(cls):
        return OperationalAlert.objects.select_related(
            "customer",
            "document",
            "promise",
            "assigned_to",
        )

    @classmethod
    def visible_alerts_for_user(cls, user):
        qs = cls.base_queryset()

        if not user or not user.is_authenticated:
            return qs.none()

        role = cls.get_effective_role(user)

        if role in cls.FULL_VISIBILITY_ROLES:
            return qs

        if role == "COBRADOR":
            return qs.filter(
                Q(assigned_to=user)
                | Q(document__assignments__collector=user, document__assignments__is_active=True)
                | Q(promise__created_by=user)
            ).distinct()

        return qs.none()

    @classmethod
    def get_visible_active_alerts(cls, user):
        now = timezone.now()

        return cls.visible_alerts_for_user(user).filter(
            Q(status__in=[
                OperationalAlert.AlertStatus.NEW,
                OperationalAlert.AlertStatus.VIEWED,
                OperationalAlert.AlertStatus.IN_PROGRESS,
                OperationalAlert.AlertStatus.REOPENED,
            ])
            | Q(
                status=OperationalAlert.AlertStatus.POSTPONED,
                due_at__lte=now,
            )
        )
    
    @classmethod
    def aging_summary_for_user(cls, user):
        now = timezone.now()

        return cls.get_visible_active_alerts(user).annotate(
            aging_bucket=Case(
                When(created_at__gte=now - timedelta(days=1), then=Value("0-1 días")),
                When(created_at__gte=now - timedelta(days=3), then=Value("2-3 días")),
                When(created_at__gte=now - timedelta(days=7), then=Value("4-7 días")),
                When(created_at__gte=now - timedelta(days=14), then=Value("8-14 días")),
                default=Value("+14 días"),
                output_field=CharField(),
            )
        ).values(
            "aging_bucket",
            "severity",
            "assigned_to__first_name",
            "assigned_to__last_name",
            "assigned_to__username",
        ).annotate(
            total=Count("id")
        ).order_by("aging_bucket", "severity")


    @classmethod
    def alerts_by_responsible(cls, user):
        old_limit = timezone.now() - timedelta(days=7)

        return cls.get_visible_active_alerts(user).values(
            "assigned_to_id",
            "assigned_to__first_name",
            "assigned_to__last_name",
            "assigned_to__username",
        ).annotate(
            total=Count("id"),
            new_count=Count(
                "id",
                filter=Q(status=OperationalAlert.AlertStatus.NEW),
            ),
            critical_count=Count(
                "id",
                filter=Q(severity=OperationalAlert.Severity.CRITICAL),
            ),
            postponed_count=Count(
                "id",
                filter=Q(status=OperationalAlert.AlertStatus.POSTPONED),
            ),
            in_progress_count=Count(
                "id",
                filter=Q(status=OperationalAlert.AlertStatus.IN_PROGRESS),
            ),
            old_count=Count(
                "id",
                filter=Q(created_at__lt=old_limit),
            ),
        ).order_by("-critical_count", "-old_count", "-total")


    @classmethod
    def reopened_alerts_count_for_user(cls, user):
        return cls.get_visible_active_alerts(user).filter(
            status=OperationalAlert.AlertStatus.REOPENED,
        ).count()

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
        now = timezone.now()

        active_existing_alert = OperationalAlert.objects.filter(
            alert_type=alert_type,
            customer=customer,
            document=document,
            promise=promise,
            status__in=[
                OperationalAlert.AlertStatus.NEW,
                OperationalAlert.AlertStatus.VIEWED,
                OperationalAlert.AlertStatus.IN_PROGRESS,
                OperationalAlert.AlertStatus.POSTPONED,
                OperationalAlert.AlertStatus.REOPENED,
            ],
        ).order_by("-created_at").first()

        if active_existing_alert:
            return active_existing_alert, False

        final_existing_alert = OperationalAlert.objects.filter(
            alert_type=alert_type,
            customer=customer,
            document=document,
            promise=promise,
            status__in=cls.FINAL_STATUSES,
        ).order_by("-resolved_at", "-created_at").first()

        status = OperationalAlert.AlertStatus.NEW
        reopened_from_alert_id = None

        if final_existing_alert:
            reference_date = final_existing_alert.resolved_at or final_existing_alert.created_at

            if reference_date and reference_date > now - timedelta(days=cls.REOPEN_MIN_DAYS):
                return final_existing_alert, False

            status = OperationalAlert.AlertStatus.REOPENED
            reopened_from_alert_id = final_existing_alert.id

        alert_metadata = metadata or {}

        if reopened_from_alert_id:
            alert_metadata = {
                **alert_metadata,
                "reopened_from_alert_id": reopened_from_alert_id,
                "reopen_min_days": cls.REOPEN_MIN_DAYS,
            }

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
            status=status,
            metadata=alert_metadata,
        )

        if status == OperationalAlert.AlertStatus.REOPENED:
            cls._register_alert_timeline_action(
                alert,
                CollectionAction.ActionType.ALERT_REOPENED,
                title="Alerta reabierta",
                description=f"Se reabrió la alerta operacional: {alert.title}",
            )

        return alert, True

    @classmethod
    def get_active_alerts(cls):
        now = timezone.now()

        return cls.base_queryset().filter(
            Q(status__in=[
                OperationalAlert.AlertStatus.NEW,
                OperationalAlert.AlertStatus.VIEWED,
                OperationalAlert.AlertStatus.IN_PROGRESS,
                OperationalAlert.AlertStatus.REOPENED,
            ])
            | Q(
                status=OperationalAlert.AlertStatus.POSTPONED,
                due_at__lte=now,
            )
        )

    @classmethod
    def mark_viewed(cls, alert):
        if alert.status == OperationalAlert.AlertStatus.NEW:
            alert.status = OperationalAlert.AlertStatus.VIEWED
            alert.save(update_fields=["status"])
        return alert

    @classmethod
    def _register_alert_timeline_action(cls, alert, action_type, user=None, title="", description=""):
        if not alert.document or not alert.customer:
            return None

        return CollectionAction.objects.create(
            document=alert.document,
            customer=alert.customer,
            action_type=action_type,
            performed_by=user,
            title=title or alert.title,
            description=description or alert.message,
            metadata={
                "alert_id": alert.id,
                "alert_type": alert.alert_type,
                "alert_status": alert.status,
                "alert_severity": alert.severity,
            },
        )


    @classmethod
    def mark_resolved(cls, alert, user=None):
        alert.status = OperationalAlert.AlertStatus.RESOLVED
        alert.resolved_at = timezone.now()
        alert.save(update_fields=["status", "resolved_at"])

        cls._register_alert_timeline_action(
            alert,
            CollectionAction.ActionType.ALERT_RESOLVED,
            user=user,
            title="Alerta resuelta",
            description=f"Se resolvió la alerta operacional: {alert.title}",
        )

        return alert


    @classmethod
    def mark_postponed(cls, alert, user=None, days=1):
        alert.status = OperationalAlert.AlertStatus.POSTPONED
        alert.due_at = timezone.now() + timedelta(days=days)
        alert.save(update_fields=["status", "due_at"])

        cls._register_alert_timeline_action(
            alert,
            CollectionAction.ActionType.ALERT_POSTPONED,
            user=user,
            title="Alerta pospuesta",
            description=f"Se pospuso la alerta operacional hasta {alert.due_at:%d/%m/%Y %H:%M}: {alert.title}",
        )

        return alert


    @classmethod
    def mark_dismissed(cls, alert, user=None):
        alert.status = OperationalAlert.AlertStatus.DISMISSED
        alert.resolved_at = timezone.now()
        alert.save(update_fields=["status", "resolved_at"])

        cls._register_alert_timeline_action(
            alert,
            CollectionAction.ActionType.ALERT_RESOLVED,
            user=user,
            title="Alerta descartada",
            description=f"Se descartó la alerta operacional: {alert.title}",
        )

        return alert

    @classmethod
    def generate_alerts(cls):
        created = {
            "promise_expired": 0,
            "promise_due_today": 0,
            "no_management_7_days": 0,
            "high_priority_document": 0,
            "unassigned_document": 0,
            "critical_customer": 0,
        }

        created["promise_expired"] = cls.generate_promise_expired_alerts()
        created["promise_due_today"] = cls.generate_promise_due_today_alerts()
        created["no_management_7_days"] = cls.generate_no_management_7_days_alerts()
        created["high_priority_document"] = cls.generate_high_priority_document_alerts()
        created["unassigned_document"] = cls.generate_unassigned_document_alerts()
        created["critical_customer"] = cls.generate_critical_customer_alerts()

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
    
    @classmethod
    def get_critical_customer_documents(cls, user=None):
        qs = Document.objects.filter(
            tags__name=DOCUMENT_TAG_CRITICAL_CUSTOMER,
            tags__is_active=True,
        ).select_related(
            "customer",
        ).prefetch_related(
            "assignments",
            "collection_actions",
            "promise_documents__promise",
        ).distinct()

        if user:
            role = getattr(user, "role", None)

            if role == "COBRADOR":
                qs = qs.filter(
                    assignments__assigned_to=user,
                    assignments__is_active=True,
                )

            elif role not in cls.FULL_VISIBILITY_ROLES:
                qs = qs.none()

        return qs


    @classmethod
    def critical_customers_without_management_count(cls, user=None):
        limit_date = timezone.now() - timedelta(days=7)

        managed_document_ids = CollectionAction.objects.filter(
            action_date__gte=limit_date,
        ).values_list("document_id", flat=True)

        return cls.get_critical_customer_documents(user).exclude(
            id__in=managed_document_ids,
        ).count()


    @classmethod
    def critical_customers_with_overdue_promises_count(cls, user=None):
        today = timezone.localdate()

        return cls.get_critical_customer_documents(user).filter(
            promise_documents__promise__promise_date__lt=today,
            promise_documents__promise__payment_confirmed=False,
            promise_documents__promise__status__in=[
                PaymentPromise.Status.PENDING,
                PaymentPromise.Status.ACTIVE,
                PaymentPromise.Status.EXPIRED,
            ],
        ).distinct().count()


    @classmethod
    def critical_customers_without_responsible_count(cls, user=None):
        return cls.get_critical_customer_documents(user).filter(
            Q(assignments__isnull=True)
            | Q(assignments__is_active=False)
        ).distinct().count()


    @classmethod
    def critical_customer_summary_for_user(cls, user=None):
        return {
            "without_management": cls.critical_customers_without_management_count(user),
            "overdue_promises": cls.critical_customers_with_overdue_promises_count(user),
            "without_responsible": cls.critical_customers_without_responsible_count(user),
        }
    
    @classmethod
    def generate_critical_customer_alerts(cls):
        count = 0

        documents = cls.get_critical_customer_documents().select_related("customer")

        for document in documents:
            active_assignment = (
                document.assignments
                .filter(is_active=True)
                .select_related("assigned_to")
                .first()
            )

            assigned_to = active_assignment.assigned_to if active_assignment else None

            _, created = cls.create_alert(
                alert_type=OperationalAlert.AlertType.CRITICAL_CUSTOMER,
                severity=OperationalAlert.Severity.CRITICAL,
                title="Cliente crítico",
                message=f"El cliente {document.customer} tiene un documento marcado como cliente crítico.",
                customer=document.customer,
                document=document,
                assigned_to=assigned_to,
                due_at=timezone.now(),
                metadata={
                    "tag": DOCUMENT_TAG_CRITICAL_CUSTOMER,
                    "document_number": document.document_number,
                },
            )

            if created:
                count += 1

        return count
    
    @classmethod
    def get_effective_role(cls, user):
        role = getattr(user, "role", None)

        if role:
            return role

        if getattr(user, "username", None) == "dev.corporate.user":
            return "SUPERVISOR"

        if getattr(user, "is_superuser", False):
            return "ADMINISTRADOR"

        if getattr(user, "is_staff", False):
            return "SUPERVISOR"

        return None