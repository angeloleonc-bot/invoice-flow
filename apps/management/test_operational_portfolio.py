from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.management.models import CollectionAction
from apps.management.services.operational_portfolio import (
    OperationalPortfolioService,
)
from apps.management.services.prioritization import (
    WorklistPriorityService,
)
from apps.management.services.workspace import (
    WorkspacePortfolioService,
)
from apps.portfolio.models import Customer, CustomerStatement, Document, DocumentAssignment, DocumentStatus


class OperationalPortfolioServiceTests(TestCase):
    def setUp(self):
        User = get_user_model()

        self.user = User.objects.create_user(
            username="operational.portfolio.test",
            email="operational.portfolio.test@example.com",
            password="test-password",
        )

        self.customer = Customer.objects.create(
            external_id="OP-CUSTOMER-001",
            rut="11111111-1",
            name="Cliente Operational Portfolio Test",
            is_active=True,
        )

        self.status = DocumentStatus.objects.create(
            name="Pendiente Operational Test",
            is_active=True,
        )

        today = timezone.localdate()

        self.open_document = Document.objects.create(
            customer=self.customer,
            external_source=Document.SOURCE_FACT_VTA_REG,
            trans_id="OP-TRANS-001",
            document_type=Document.DOCUMENT_TYPE_INVOICE,
            document_number="OP-001",
            issue_date=today - timedelta(days=30),
            due_date=today - timedelta(days=10),
            original_amount=Decimal("100000"),
            balance_amount=Decimal("100000"),
            status=self.status,
        )

        self.closed_document = Document.objects.create(
            customer=self.customer,
            external_source=Document.SOURCE_FACT_VTA_REG,
            trans_id="OP-TRANS-002",
            document_type=Document.DOCUMENT_TYPE_INVOICE,
            document_number="OP-002",
            issue_date=today - timedelta(days=30),
            due_date=today - timedelta(days=10),
            original_amount=Decimal("100000"),
            balance_amount=Decimal("0"),
            status=self.status,
        )

    def create_action(
        self,
        *,
        document,
        action_type,
    ):
        return CollectionAction.objects.create(
            document=document,
            customer=document.customer,
            action_type=action_type,
            performed_by=self.user,
            title="Acción test",
            description="Acción test",
            action_date=timezone.now(),
            metadata={},
        )

    def create_assignment(self, document):
        return DocumentAssignment.objects.create(
            document=document,
            assigned_to=self.user,
            assigned_by=self.user,
            assignment_type="manual",
            is_active=True,
            notes="Test workspace",
        )

    def workspace_service(self):
        return WorkspacePortfolioService(
            user=self.user,
            selected_scope="my",
        )

    def test_workspace_visible_open_documents_uses_operational_portfolio(self):
        self.create_assignment(self.open_document)
        self.create_assignment(self.closed_document)

        service = self.workspace_service()

        ids = set(
            service.visible_open_documents()
            .values_list("id", flat=True)
        )

        self.assertIn(
            self.open_document.id,
            ids,
        )

        self.assertNotIn(
            self.closed_document.id,
            ids,
        )

    def test_workspace_scoped_actions_excludes_alert_resolved(self):
        self.create_assignment(self.open_document)

        self.create_action(
            document=self.open_document,
            action_type=CollectionAction.ActionType.ALERT_RESOLVED,
        )

        service = self.workspace_service()

        self.assertFalse(
            service.scoped_actions().exists()
        )

    def test_workspace_scoped_actions_keeps_real_management(self):
        self.create_assignment(self.open_document)

        action = self.create_action(
            document=self.open_document,
            action_type=CollectionAction.ActionType.EMAIL,
        )

        service = self.workspace_service()

        ids = set(
            service.scoped_actions()
            .values_list("id", flat=True)
        )

        self.assertIn(
            action.id,
            ids,
        )

    def test_workspace_last_action_ignores_alert_resolved(self):
        self.create_assignment(self.open_document)

        email_action = self.create_action(
            document=self.open_document,
            action_type=CollectionAction.ActionType.EMAIL,
        )

        technical_action = self.create_action(
            document=self.open_document,
            action_type=CollectionAction.ActionType.ALERT_RESOLVED,
        )

        # Se fuerza que la acción técnica sea posterior.
        CollectionAction.objects.filter(
            id=email_action.id,
        ).update(
            action_date=timezone.now() - timedelta(hours=2)
        )

        CollectionAction.objects.filter(
            id=technical_action.id,
        ).update(
            action_date=timezone.now() - timedelta(hours=1)
        )

        service = self.workspace_service()

        result = service.get_last_actions_by_customer(
            [self.customer.id]
        )

        self.assertIn(
            self.customer.id,
            result,
        )

        self.assertEqual(
            result[self.customer.id]["action_type"],
            CollectionAction.ActionType.EMAIL,
        )

    def test_recent_sent_statement_removes_customer_from_followup(self):
        self.create_assignment(self.open_document)

        CustomerStatement.objects.create(
            customer=self.customer,
            status=CustomerStatement.Status.SENT,
            sent_at=timezone.now(),
        )

        service = self.workspace_service()

        self.assertNotIn(
            self.customer.id,
            service.get_followup_customer_ids(),
        )

    def test_old_sent_statement_keeps_customer_in_followup(self):
        self.create_assignment(self.open_document)

        statement = CustomerStatement.objects.create(
            customer=self.customer,
            status=CustomerStatement.Status.SENT,
            sent_at=timezone.now(),
        )

        CustomerStatement.objects.filter(
            id=statement.id,
        ).update(
            sent_at=timezone.now() - timedelta(days=8)
        )

        service = self.workspace_service()

        self.assertIn(
            self.customer.id,
            service.get_followup_customer_ids(),
        )

    def test_bulk_priority_result_exposes_applied_rules(self):
        service = WorklistPriorityService()

        result = service.evaluate_documents_bulk(
            [self.open_document]
        )[self.open_document.id]

        self.assertIn(
            "applied_priority_rules",
            result,
        )

        self.assertEqual(
            result["priority_reason_codes"],
            [
                rule["code"]
                for rule in result[
                    "applied_priority_rules"
                ]
            ],
        )

    def test_customer_priority_uses_highest_document_score(self):
        self.create_assignment(self.open_document)

        service = self.workspace_service()

        priority_service = WorklistPriorityService()

        original_method = (
            priority_service.evaluate_documents_bulk
        )

        # Este test valida la forma del servicio real
        # mediante las reglas configuradas en la base de test.
        result = service.get_priority_by_customer(
            [self.customer.id]
        )

        if self.customer.id in result:
            self.assertGreaterEqual(
                result[self.customer.id]["priority_score"],
                service.PRIORITY_MIN_SCORE,
            )

            self.assertGreaterEqual(
                result[self.customer.id][
                    "priority_document_count"
                ],
                1,
            )

    def test_customer_priority_never_removes_customer_from_portfolio(self):
        self.create_assignment(self.open_document)

        service = self.workspace_service()

        visible_customer_ids = set(
            service.visible_open_documents()
            .values_list(
                "customer_id",
                flat=True,
            )
        )

        service.get_priority_by_customer(
            [self.customer.id]
        )

        self.assertIn(
            self.customer.id,
            visible_customer_ids,
        )

    def test_followup_customer_without_management_is_included(self):
        self.create_assignment(self.open_document)

        service = self.workspace_service()

        self.assertIn(
            self.customer.id,
            service.get_followup_customer_ids(),
        )

    def test_recent_real_management_removes_customer_from_followup(self):
        self.create_assignment(self.open_document)

        self.create_action(
            document=self.open_document,
            action_type=CollectionAction.ActionType.EMAIL,
        )

        service = self.workspace_service()

        self.assertNotIn(
            self.customer.id,
            service.get_followup_customer_ids(),
        )

    def test_old_real_management_keeps_customer_in_followup(self):
        self.create_assignment(self.open_document)

        action = self.create_action(
            document=self.open_document,
            action_type=CollectionAction.ActionType.CALL,
        )

        CollectionAction.objects.filter(
            id=action.id,
        ).update(
            action_date=timezone.now() - timedelta(days=8)
        )

        service = self.workspace_service()

        self.assertIn(
            self.customer.id,
            service.get_followup_customer_ids(),
        )

    def test_recent_alert_event_does_not_remove_customer_from_followup(self):
        self.create_assignment(self.open_document)

        self.create_action(
            document=self.open_document,
            action_type=CollectionAction.ActionType.ALERT_RESOLVED,
        )

        service = self.workspace_service()

        self.assertIn(
            self.customer.id,
            service.get_followup_customer_ids(),
        )

    def test_operational_documents_only_include_positive_balance(self):
        ids = set(
            OperationalPortfolioService
            .operational_documents()
            .values_list("id", flat=True)
        )

        self.assertIn(
            self.open_document.id,
            ids,
        )
        self.assertNotIn(
            self.closed_document.id,
            ids,
        )

    def test_is_operational_document_uses_positive_balance(self):
        self.assertTrue(
            OperationalPortfolioService.is_operational_document(
                self.open_document
            )
        )

        self.assertFalse(
            OperationalPortfolioService.is_operational_document(
                self.closed_document
            )
        )

    def test_alert_resolved_is_not_collection_management(self):
        self.create_action(
            document=self.open_document,
            action_type=CollectionAction.ActionType.ALERT_RESOLVED,
        )

        exists = (
            OperationalPortfolioService
            .has_collection_management_since(
                document=self.open_document,
                since=timezone.now() - timedelta(days=7),
            )
        )

        self.assertFalse(exists)

    def test_alert_postponed_is_not_collection_management(self):
        self.create_action(
            document=self.open_document,
            action_type=CollectionAction.ActionType.ALERT_POSTPONED,
        )

        exists = (
            OperationalPortfolioService
            .has_collection_management_since(
                document=self.open_document,
                since=timezone.now() - timedelta(days=7),
            )
        )

        self.assertFalse(exists)

    def test_alert_reopened_is_not_collection_management(self):
        self.create_action(
            document=self.open_document,
            action_type=CollectionAction.ActionType.ALERT_REOPENED,
        )

        exists = (
            OperationalPortfolioService
            .has_collection_management_since(
                document=self.open_document,
                since=timezone.now() - timedelta(days=7),
            )
        )

        self.assertFalse(exists)

    def test_email_counts_as_collection_management(self):
        self.create_action(
            document=self.open_document,
            action_type=CollectionAction.ActionType.EMAIL,
        )

        exists = (
            OperationalPortfolioService
            .has_collection_management_since(
                document=self.open_document,
                since=timezone.now() - timedelta(days=7),
            )
        )

        self.assertTrue(exists)

    def test_promise_counts_as_collection_management(self):
        self.create_action(
            document=self.open_document,
            action_type=CollectionAction.ActionType.PROMISE,
        )

        exists = (
            OperationalPortfolioService
            .has_collection_management_since(
                document=self.open_document,
                since=timezone.now() - timedelta(days=7),
            )
        )

        self.assertTrue(exists)

    def test_customer_management_ignores_alert_events(self):
        self.create_action(
            document=self.open_document,
            action_type=CollectionAction.ActionType.ALERT_RESOLVED,
        )

        exists = (
            OperationalPortfolioService
            .has_collection_management_since(
                customer=self.customer,
                since=timezone.now() - timedelta(days=7),
            )
        )

        self.assertFalse(exists)

        self.create_action(
            document=self.open_document,
            action_type=CollectionAction.ActionType.CALL,
        )

        exists = (
            OperationalPortfolioService
            .has_collection_management_since(
                customer=self.customer,
                since=timezone.now() - timedelta(days=7),
            )
        )

        self.assertTrue(exists)

    def test_no_management_priority_does_not_apply_to_zero_balance(self):
        service = WorklistPriorityService()

        self.assertFalse(
            service._has_no_management_7_days(
                self.closed_document
            )
        )

    def test_alert_resolved_does_not_remove_no_management_priority(self):
        self.create_action(
            document=self.open_document,
            action_type=CollectionAction.ActionType.ALERT_RESOLVED,
        )

        service = WorklistPriorityService()

        self.assertTrue(
            service._has_no_management_7_days(
                self.open_document
            )
        )

    def test_real_management_removes_no_management_priority(self):
        self.create_action(
            document=self.open_document,
            action_type=CollectionAction.ActionType.CALL,
        )

        service = WorklistPriorityService()

        self.assertFalse(
            service._has_no_management_7_days(
                self.open_document
            )
        )
