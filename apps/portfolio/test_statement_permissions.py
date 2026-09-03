from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.portfolio.models import (
    Customer,
    Document,
    DocumentAssignment,
    DocumentStatus,
    DocumentSubStatus,
)
from apps.portfolio.statement_views import _can_send_statement


class CustomerStatementPermissionTests(TestCase):
    def setUp(self):
        User = get_user_model()

        self.customer = Customer.objects.create(
            external_id="STATEMENT-PERM-001",
            rut="99999996-6",
            name="Cliente Permisos Statement",
        )

        self.collector = User.objects.create_user(
            username="collector-statement",
            email="collector.statement@example.com",
            password="test-password",
        )

        self.other_collector = User.objects.create_user(
            username="other-collector-statement",
            email="other.collector@example.com",
            password="test-password",
        )

        self.supervisor = User.objects.create_user(
            username="supervisor-statement",
            email="supervisor.statement@example.com",
            password="test-password",
        )

    @patch(
        "apps.portfolio.statement_views."
        "RoleService.get_effective_role_code"
    )
    def test_supervisor_is_allowed(
        self,
        role_mock,
    ):
        role_mock.return_value = "SUPERVISOR"

        self.assertTrue(
            _can_send_statement(
                self.supervisor,
                self.customer,
            )
        )

    @patch(
        "apps.portfolio.statement_views."
        "UpcomingStatementRepository.is_assigned_to_collector"
    )
    @patch(
        "apps.portfolio.statement_views."
        "RoleService.get_effective_role_code"
    )
    def test_collector_with_operational_assignment_is_allowed(
        self,
        role_mock,
        upcoming_mock,
    ):
        role_mock.return_value = "COBRADOR"
        upcoming_mock.return_value = False

        status = DocumentStatus.objects.create(
            name="Abierto Permiso Statement",
            is_active=True,
        )

        substatus = DocumentSubStatus.objects.create(
            name="Normal Permiso Statement",
            is_active=True,
        )

        document = Document.objects.create(
            customer=self.customer,
            external_source=Document.SOURCE_FACT_VTA_REG,
            trans_id="STATEMENT-PERM-TRANS-001",
            source_doc_entry="STATEMENT-PERM-ENTRY-001",
            document_type="Factura",
            document_number="STATEMENT-PERM-001",
            issue_date=timezone.localdate(),
            due_date=timezone.localdate(),
            original_amount=Decimal("1000.00"),
            balance_amount=Decimal("1000.00"),
            overpayment_amount=Decimal("0"),
            status=status,
            sub_status=substatus,
        )

        DocumentAssignment.objects.create(
            document=document,
            assigned_to=self.collector,
            assigned_by=self.collector,
            assignment_type=(
                DocumentAssignment.ASSIGNMENT_TYPE_INITIAL
            ),
        )

        self.assertTrue(
            _can_send_statement(
                self.collector,
                self.customer,
            )
        )

        upcoming_mock.assert_not_called()

    @patch(
        "apps.portfolio.statement_views."
        "UpcomingStatementRepository.is_assigned_to_collector"
    )
    @patch(
        "apps.portfolio.statement_views."
        "RoleService.get_effective_role_code"
    )
    def test_collector_with_upcoming_assignment_is_allowed(
        self,
        role_mock,
        upcoming_mock,
    ):
        role_mock.return_value = "COBRADOR"
        upcoming_mock.return_value = True

        self.assertTrue(
            _can_send_statement(
                self.collector,
                self.customer,
            )
        )

        upcoming_mock.assert_called_once()

        kwargs = upcoming_mock.call_args.kwargs

        self.assertEqual(
            kwargs["customer"],
            self.customer,
        )

        self.assertEqual(
            kwargs["collector_email"],
            self.collector.email,
        )

        self.assertEqual(
            kwargs["as_of_date"],
            timezone.localdate(),
        )

    @patch(
        "apps.portfolio.statement_views."
        "UpcomingStatementRepository.is_assigned_to_collector"
    )
    @patch(
        "apps.portfolio.statement_views."
        "RoleService.get_effective_role_code"
    )
    def test_other_collector_is_denied(
        self,
        role_mock,
        upcoming_mock,
    ):
        role_mock.return_value = "COBRADOR"
        upcoming_mock.return_value = False

        self.assertFalse(
            _can_send_statement(
                self.other_collector,
                self.customer,
            )
        )
