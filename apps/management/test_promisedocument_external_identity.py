from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.management.models import (
    PaymentPromise,
    PromiseDocument,
)
from apps.portfolio.models import (
    Customer,
    Document,
    DocumentAssignment,
    DocumentStatus,
)


class PromiseDocumentExternalIdentityTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            external_id="TEST-CUSTOMER-001",
            rut="99999999-9",
            name="Cliente Test Promesas",
        )

        self.status = DocumentStatus.objects.create(
            name="Pendiente Test Promesas",
            is_active=True,
            sort_order=1,
        )

        self.document = Document.objects.create(
            customer=self.customer,
            trans_id="TRANS-TEST-001",
            document_type=Document.DOCUMENT_TYPE_INVOICE,
            document_number="100001",
            source_doc_entry="500001",
            issue_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=10),
            original_amount=Decimal("100000.00"),
            balance_amount=Decimal("100000.00"),
            status=self.status,
            external_source=Document.SOURCE_FACT_VTA_REG,
        )

        self.promise = PaymentPromise.objects.create(
            customer=self.customer,
            promise_date=timezone.localdate() + timedelta(days=5),
            promised_amount=Decimal("100000.00"),
            status=PaymentPromise.Status.ACTIVE,
        )

    def test_historical_relation_with_document_remains_valid(self):
        relation = PromiseDocument(
            promise=self.promise,
            document=self.document,
        )

        relation.full_clean()
        relation.save()

        self.assertEqual(relation.document_id, self.document.id)

    def test_valid_external_relation_without_document(self):
        relation = PromiseDocument(
            promise=self.promise,
            document=None,
            source="Fact_Vta_Sin_Vencer",
            source_customer_external_id=self.customer.external_id,
            source_trans_id="TRANS-UPCOMING-001",
            source_doc_entry="DOCENTRY-UPCOMING-001",
            source_document_number="200001",
            source_due_date=timezone.localdate() + timedelta(days=20),
            source_original_amount=Decimal("250000.00"),
            source_balance_amount=Decimal("250000.00"),
        )

        relation.full_clean()
        relation.save()

        self.assertIsNone(relation.document_id)

    def test_external_relation_without_identity_is_invalid(self):
        relation = PromiseDocument(
            promise=self.promise,
            document=None,
        )

        with self.assertRaises(ValidationError):
            relation.full_clean()

    def test_duplicate_external_relation_in_same_promise_is_invalid(self):
        base_values = {
            "promise": self.promise,
            "document": None,
            "source": "Fact_Vta_Sin_Vencer",
            "source_customer_external_id": self.customer.external_id,
            "source_trans_id": "TRANS-UPCOMING-002",
            "source_doc_entry": "DOCENTRY-UPCOMING-002",
            "source_document_number": "200002",
            "source_due_date": timezone.localdate() + timedelta(days=20),
            "source_original_amount": Decimal("300000.00"),
            "source_balance_amount": Decimal("300000.00"),
        }

        first = PromiseDocument(**base_values)
        first.full_clean()
        first.save()

        second = PromiseDocument(**base_values)

        with self.assertRaises(ValidationError):
            second.full_clean()

    def test_same_external_invoice_can_exist_in_different_promises(self):
        first = PromiseDocument(
            promise=self.promise,
            document=None,
            source="Fact_Vta_Sin_Vencer",
            source_customer_external_id=self.customer.external_id,
            source_trans_id="TRANS-UPCOMING-003",
            source_doc_entry="DOCENTRY-UPCOMING-003",
            source_document_number="200003",
        )

        first.full_clean()
        first.save()

        second_promise = PaymentPromise.objects.create(
            customer=self.customer,
            promise_date=timezone.localdate() + timedelta(days=15),
            promised_amount=Decimal("50000.00"),
            status=PaymentPromise.Status.ACTIVE,
        )

        second = PromiseDocument(
            promise=second_promise,
            document=None,
            source="Fact_Vta_Sin_Vencer",
            source_customer_external_id=self.customer.external_id,
            source_trans_id="TRANS-UPCOMING-003",
            source_doc_entry="DOCENTRY-UPCOMING-003",
            source_document_number="200003",
        )

        second.full_clean()
        second.save()

        self.assertEqual(PromiseDocument.objects.count(), 2)

    def test_snapshot_fields_do_not_modify_document(self):
        original_due_date = self.document.due_date
        original_amount = self.document.original_amount
        original_balance = self.document.balance_amount

        relation = PromiseDocument(
            promise=self.promise,
            document=None,
            source="Fact_Vta_Sin_Vencer",
            source_customer_external_id=self.customer.external_id,
            source_trans_id="TRANS-UPCOMING-004",
            source_doc_entry="DOCENTRY-UPCOMING-004",
            source_document_number="200004",
            source_due_date=timezone.localdate() + timedelta(days=90),
            source_original_amount=Decimal("999999.00"),
            source_balance_amount=Decimal("888888.00"),
        )

        relation.full_clean()
        relation.save()

        self.document.refresh_from_db()

        self.assertEqual(self.document.due_date, original_due_date)
        self.assertEqual(self.document.original_amount, original_amount)
        self.assertEqual(self.document.balance_amount, original_balance)

    def test_external_relation_does_not_create_document(self):
        before = Document.objects.count()

        relation = PromiseDocument(
            promise=self.promise,
            document=None,
            source="Fact_Vta_Sin_Vencer",
            source_customer_external_id=self.customer.external_id,
            source_trans_id="TRANS-UPCOMING-005",
            source_doc_entry="DOCENTRY-UPCOMING-005",
            source_document_number="200005",
        )

        relation.full_clean()
        relation.save()

        self.assertEqual(Document.objects.count(), before)

    def test_external_relation_does_not_create_assignment(self):
        before = DocumentAssignment.objects.count()

        relation = PromiseDocument(
            promise=self.promise,
            document=None,
            source="Fact_Vta_Sin_Vencer",
            source_customer_external_id=self.customer.external_id,
            source_trans_id="TRANS-UPCOMING-006",
            source_doc_entry="DOCENTRY-UPCOMING-006",
            source_document_number="200006",
        )

        relation.full_clean()
        relation.save()

        self.assertEqual(DocumentAssignment.objects.count(), before)

    def test_external_relation_does_not_change_document_balance(self):
        before = self.document.balance_amount

        relation = PromiseDocument(
            promise=self.promise,
            document=None,
            source="Fact_Vta_Sin_Vencer",
            source_customer_external_id=self.customer.external_id,
            source_trans_id="TRANS-UPCOMING-007",
            source_doc_entry="DOCENTRY-UPCOMING-007",
            source_document_number="200007",
        )

        relation.full_clean()
        relation.save()

        self.document.refresh_from_db()

        self.assertEqual(self.document.balance_amount, before)

    def test_external_relation_does_not_change_document_status(self):
        before_status_id = self.document.status_id
        before_sub_status_id = self.document.sub_status_id

        relation = PromiseDocument(
            promise=self.promise,
            document=None,
            source="Fact_Vta_Sin_Vencer",
            source_customer_external_id=self.customer.external_id,
            source_trans_id="TRANS-UPCOMING-008",
            source_doc_entry="DOCENTRY-UPCOMING-008",
            source_document_number="200008",
        )

        relation.full_clean()
        relation.save()

        self.document.refresh_from_db()

        self.assertEqual(self.document.status_id, before_status_id)
        self.assertEqual(
            self.document.sub_status_id,
            before_sub_status_id,
        )
