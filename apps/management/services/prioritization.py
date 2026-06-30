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

        # AGREGAR ESTA LÍNEA
        self.recent_management_limit = timezone.now() - timedelta(days=7)

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

    def evaluate_documents_bulk(self, documents):
        documents = list(documents)

        open_documents = [
            document for document in documents
            if self._is_open_document(document)
        ]

        document_ids = [document.id for document in open_documents if document.id]

        expired_promise_document_ids = self._get_expired_promise_document_ids(document_ids)
        recently_managed_document_ids = self._get_recently_managed_document_ids(document_ids)

        results = {}

        for document in documents:
            if not self._is_open_document(document):
                results[document.id] = self._empty_result()
                continue

            applied_rules = []

            if document.id in expired_promise_document_ids:
                self._append_rule(applied_rules, self.RULE_PROMISE_EXPIRED)

            if self._is_document_overdue(document):
                self._append_rule(applied_rules, self.RULE_DOCUMENT_OVERDUE)

            if self._is_high_balance(document):
                self._append_rule(applied_rules, self.RULE_HIGH_BALANCE)

            if document.id not in recently_managed_document_ids:
                self._append_rule(applied_rules, self.RULE_NO_MANAGEMENT_7_DAYS)

            results[document.id] = self._build_result(applied_rules)

        return results

    def _get_expired_promise_document_ids(self, document_ids):
        if not document_ids:
            return set()

        return set(
            PaymentPromise.objects.filter(
                promise_documents__document_id__in=document_ids,
                status__in=self.OPEN_PROMISE_STATUSES,
                payment_confirmed=False,
                promise_date__lt=self.today,
            )
            .values_list("promise_documents__document_id", flat=True)
            .distinct()
        )

    def _get_recently_managed_document_ids(self, document_ids):
        if not document_ids:
            return set()

        return set(
            CollectionAction.objects.filter(
                document_id__in=document_ids,
                action_date__gte=self.recent_management_limit,
            )
            .values_list("document_id", flat=True)
            .distinct()
        )

    def _append_rule(self, applied_rules, rule_code):
        rule = self.rules.get(rule_code)
        if not rule:
            return

        applied_rules.append(
            {
                "code": rule.code,
                "name": rule.name,
                "score": rule.score or 0,
                "evaluation_order": rule.evaluation_order,
            }
        )

    def _build_result(self, applied_rules):
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

    def _empty_result(self):
        return {
            "priority_score": 0,
            "main_priority_reason": "Sin prioridad especial",
            "priority_reasons": [],
            "priority_reason_codes": [],
            "applied_priority_rules": [],
        }

    def _is_open_document(self, document):
        if not document:
            return False

        if self._balance(document) <= 0:
            return False

        status = getattr(document, "status", None)
        status_name = ""

        if status:
            status_name = (
                getattr(status, "name", None)
                or getattr(status, "label", None)
                or getattr(status, "description", None)
                or str(status)
            )

        return str(status_name).strip().upper() not in {
            "CERRADA",
            "CERRADO",
            "PAGADA",
            "PAGADO",
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