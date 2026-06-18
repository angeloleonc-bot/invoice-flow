from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from apps.management.models import CollectionAction, PaymentPromise, PriorityRule


class WorklistPriorityService:
    RULE_PROMISE_EXPIRED = "PROMISE_EXPIRED"
    RULE_PROMISE_DUE_TODAY = "PROMISE_DUE_TODAY"
    RULE_DOCUMENT_OVERDUE = "DOCUMENT_OVERDUE"
    RULE_HIGH_BALANCE = "HIGH_BALANCE"
    RULE_CUSTOMER_CRITICAL = "CUSTOMER_CRITICAL"
    RULE_NO_MANAGEMENT_7_DAYS = "NO_MANAGEMENT_7_DAYS"
    RULE_UPCOMING_DUE_DATE = "UPCOMING_DUE_DATE"
    RULE_SUPERVISOR_REQUIRED = "SUPERVISOR_REQUIRED"

    OPEN_PROMISE_STATUSES = [
        PaymentPromise.Status.PENDING,
        PaymentPromise.Status.ACTIVE,
        PaymentPromise.Status.EXPIRED,
    ]

    DEFAULT_HIGH_BALANCE_THRESHOLD = Decimal("1000000")

    def __init__(self):
        self.today = timezone.localdate()
        self.high_balance_threshold = Decimal(
            str(
                getattr(
                    settings,
                    "WORKLIST_HIGH_BALANCE_THRESHOLD",
                    self.DEFAULT_HIGH_BALANCE_THRESHOLD,
                )
            )
        )
        self.rules = {
            rule.code: rule
            for rule in PriorityRule.objects.filter(is_active=True).order_by(
                "evaluation_order",
                "code",
            )
        }

    def evaluate_assignment(self, assignment):
        return self.evaluate_document(
            assignment.document,
            assignment=assignment,
        )

    def evaluate_document(self, document, assignment=None):
        applied_rules = []

        checks = [
            (self.RULE_PROMISE_EXPIRED, self._has_expired_promise),
            (self.RULE_PROMISE_DUE_TODAY, self._has_promise_due_today),
            (self.RULE_DOCUMENT_OVERDUE, self._is_document_overdue),
            (self.RULE_HIGH_BALANCE, self._is_high_balance),
            (self.RULE_CUSTOMER_CRITICAL, self._is_customer_critical),
            (self.RULE_NO_MANAGEMENT_7_DAYS, self._has_no_management_7_days),
            (self.RULE_UPCOMING_DUE_DATE, self._has_upcoming_due_date),
            (self.RULE_SUPERVISOR_REQUIRED, self._requires_supervisor),
        ]

        for rule_code, check in checks:
            rule = self.rules.get(rule_code)
            if not rule:
                continue

            try:
                applies = check(document)
            except Exception:
                applies = False

            if applies:
                applied_rules.append(
                    {
                        "code": rule.code,
                        "name": rule.name,
                        "score": rule.score or 0,
                        "evaluation_order": rule.evaluation_order,
                    }
                )

        priority_score = sum(rule["score"] for rule in applied_rules)

        main_rule = None
        if applied_rules:
            main_rule = sorted(
                applied_rules,
                key=lambda item: (
                    -item["score"],
                    item["evaluation_order"],
                    item["code"],
                ),
            )[0]

        return {
            "priority_score": priority_score,
            "main_priority_reason": (
                main_rule["name"] if main_rule else "Sin prioridad especial"
            ),
            "priority_reasons": [rule["name"] for rule in applied_rules],
            "priority_reason_codes": [rule["code"] for rule in applied_rules],
            "applied_priority_rules": applied_rules,
        }

    def sort_items(self, items):
        return sorted(
            items,
            key=lambda item: (
                -item.get("priority_score", 0),
                item.get("document").due_date if item.get("document") else self.today,
            ),
        )

    def _open_promises_queryset(self, document):
        return PaymentPromise.objects.filter(
            promise_documents__document=document,
            status__in=self.OPEN_PROMISE_STATUSES,
            payment_confirmed=False,
        )

    def _has_expired_promise(self, document):
        return self._open_promises_queryset(document).filter(
            promise_date__lt=self.today,
        ).exists()

    def _has_promise_due_today(self, document):
        return self._open_promises_queryset(document).filter(
            promise_date=self.today,
        ).exists()

    def _is_document_overdue(self, document):
        return (
            bool(getattr(document, "due_date", None))
            and document.due_date < self.today
            and self._balance(document) > 0
        )

    def _is_high_balance(self, document):
        return self._balance(document) >= self.high_balance_threshold

    def _is_customer_critical(self, document):
        return self._document_has_tag(document, "Cliente crítico")

    def _has_no_management_7_days(self, document):
        limit_date = timezone.now() - timedelta(days=7)

        return not CollectionAction.objects.filter(
            document=document,
            action_date__gte=limit_date,
        ).exists()

    def _has_upcoming_due_date(self, document):
        if not getattr(document, "due_date", None):
            return False

        return (
            self.today <= document.due_date <= self.today + timedelta(days=7)
            and self._balance(document) > 0
        )

    def _requires_supervisor(self, document):
        return self._document_has_tag(document, "Requiere supervisor")

    def _document_has_tag(self, document, tag_name):
        if not document or not hasattr(document, "tags"):
            return False

        return document.tags.filter(name__iexact=tag_name, is_active=True).exists()

    def _balance(self, document):
        value = getattr(document, "balance_amount", 0) or 0
        return Decimal(str(value))