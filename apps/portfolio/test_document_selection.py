from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from apps.portfolio.services.customer_statements import (
    CATEGORY_OVERDUE,
    CATEGORY_UPCOMING,
    CustomerStatementService,
    StatementDocument,
)


class FakeCustomer:
    id = 10
    external_id = "C001"
    rut = "11.111.111-1"
    name = "Cliente Test"


class CustomerStatementSelectionResolverTests(SimpleTestCase):
    def setUp(self):
        self.customer = FakeCustomer()

        self.portfolio_document = StatementDocument(
            selection_key="document:101",
            source="Document",
            document_id=101,
            trans_id="T101",
            doc_entry="D101",
            document_number="F101",
            document_type="Factura",
            issue_date=date(2026, 8, 1),
            due_date=date(2026, 8, 31),
            original_amount=Decimal("100000"),
            balance_amount=Decimal("50000"),
            category=CATEGORY_OVERDUE,
            category_label="Vencido",
            days_from_due=2,
        )

        self.upcoming_document = StatementDocument(
            selection_key="upcoming:T202:D202:F202",
            source="Fact_Vta_Sin_Vencer",
            document_id=None,
            trans_id="T202",
            doc_entry="D202",
            document_number="F202",
            document_type="Factura",
            issue_date=date(2026, 8, 20),
            due_date=date(2026, 9, 20),
            original_amount=Decimal("250000"),
            balance_amount=Decimal("250000"),
            category=CATEGORY_UPCOMING,
            category_label="Por vencer",
            days_from_due=18,
        )

    def available(self):
        return [
            self.portfolio_document,
            self.upcoming_document,
        ]

    def test_resolves_mixed_selection_preserving_order(self):
        with patch.object(
            CustomerStatementService,
            "available_documents",
            return_value=self.available(),
        ):
            result = (
                CustomerStatementService
                .resolve_selected_documents(
                    customer=self.customer,
                    selected_keys=[
                        "upcoming:T202:D202:F202",
                        "document:101",
                    ],
                    as_of_date=date(2026, 9, 2),
                )
            )

        self.assertEqual(
            [item.selection_key for item in result],
            [
                "upcoming:T202:D202:F202",
                "document:101",
            ],
        )

        self.assertIsNone(result[0].document_id)
        self.assertEqual(result[1].document_id, 101)

    def test_removes_duplicate_keys_preserving_first_occurrence(self):
        with patch.object(
            CustomerStatementService,
            "available_documents",
            return_value=self.available(),
        ):
            result = (
                CustomerStatementService
                .resolve_selected_documents(
                    customer=self.customer,
                    selected_keys=[
                        "document:101",
                        "document:101",
                        "upcoming:T202:D202:F202",
                    ],
                    as_of_date=date(2026, 9, 2),
                )
            )

        self.assertEqual(len(result), 2)
        self.assertEqual(
            [item.selection_key for item in result],
            [
                "document:101",
                "upcoming:T202:D202:F202",
            ],
        )

    def test_rejects_empty_selection(self):
        with self.assertRaises(ValidationError):
            (
                CustomerStatementService
                .resolve_selected_documents(
                    customer=self.customer,
                    selected_keys=["", "   ", None],
                    as_of_date=date(2026, 9, 2),
                )
            )

    def test_rejects_unknown_document_key(self):
        with patch.object(
            CustomerStatementService,
            "available_documents",
            return_value=self.available(),
        ):
            with self.assertRaises(ValidationError):
                (
                    CustomerStatementService
                    .resolve_selected_documents(
                        customer=self.customer,
                        selected_keys=["document:999999"],
                        as_of_date=date(2026, 9, 2),
                    )
                )

    def test_rejects_unknown_upcoming_key(self):
        with patch.object(
            CustomerStatementService,
            "available_documents",
            return_value=self.available(),
        ):
            with self.assertRaises(ValidationError):
                (
                    CustomerStatementService
                    .resolve_selected_documents(
                        customer=self.customer,
                        selected_keys=[
                            "upcoming:FAKE:FAKE:FAKE"
                        ],
                        as_of_date=date(2026, 9, 2),
                    )
                )

    def test_does_not_parse_or_trust_selection_key(self):
        with patch.object(
            CustomerStatementService,
            "available_documents",
            return_value=self.available(),
        ):
            malicious_or_malformed = [
                "document:101:extra",
                "document:-1",
                "upcoming:T202:D202",
                "Document:101",
            ]

            for selection_key in malicious_or_malformed:
                with self.subTest(selection_key=selection_key):
                    with self.assertRaises(ValidationError):
                        (
                            CustomerStatementService
                            .resolve_selected_documents(
                                customer=self.customer,
                                selected_keys=[
                                    selection_key
                                ],
                                as_of_date=date(2026, 9, 2),
                            )
                        )

    def test_returns_original_common_representation(self):
        with patch.object(
            CustomerStatementService,
            "available_documents",
            return_value=self.available(),
        ):
            result = (
                CustomerStatementService
                .resolve_selected_documents(
                    customer=self.customer,
                    selected_keys=["document:101"],
                    as_of_date=date(2026, 9, 2),
                )
            )

        self.assertIs(
            result[0],
            self.portfolio_document,
        )
