from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.management.models import (
    PaymentPromise,
    PromiseDocument,
)
from apps.portfolio.models import (
    Customer,
    Document,
    DocumentStatus,
    PaymentRecord,
)
from apps.portfolio.services.payments import (
    evaluate_related_promises,
)


class MixedPromiseEvaluationRegressionTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            external_id="MIXED-EVAL-CUSTOMER",
            rut="99999998-0",
            name="Cliente Test Evaluación Mixta",
        )

        self.status = DocumentStatus.objects.create(
            name="Pendiente Test Evaluación Mixta",
            is_active=True,
            sort_order=1,
        )

        self.document = Document.objects.create(
            customer=self.customer,
            trans_id="TRANS-MIXED-EVAL-001",
            document_type=Document.DOCUMENT_TYPE_INVOICE,
            document_number="300001",
            source_doc_entry="700001",
            issue_date=timezone.localdate() - timedelta(days=40),
            due_date=timezone.localdate() - timedelta(days=10),
            original_amount=Decimal("100000.00"),
            balance_amount=Decimal("0.00"),
            status=self.status,
            external_source=Document.SOURCE_FACT_VTA_REG,
        )

        self.promise = PaymentPromise.objects.create(
            customer=self.customer,
            promise_date=timezone.localdate() + timedelta(days=5),
            promised_amount=Decimal("250000.00"),
            status=PaymentPromise.Status.ACTIVE,
            payment_confirmed=False,
        )

        self.real_relation = PromiseDocument.objects.create(
            promise=self.promise,
            document=self.document,
        )

        self.external_relation = PromiseDocument(
            promise=self.promise,
            document=None,
            source="Fact_Vta_Sin_Vencer",
            source_customer_external_id=self.customer.external_id,
            source_trans_id="TRANS-UPCOMING-EVAL-001",
            source_doc_entry="DOCENTRY-UPCOMING-EVAL-001",
            source_document_number="400001",
            source_due_date=timezone.localdate() + timedelta(days=20),
            source_original_amount=Decimal("150000.00"),
            source_balance_amount=Decimal("150000.00"),
        )

        self.external_relation.full_clean()
        self.external_relation.save()

        PaymentRecord.objects.create(
            document=self.document,
            customer=self.customer,
            payment_date=timezone.localdate(),
            amount=Decimal("100000.00"),
            source_reference="TEST-MIXED-EVALUATION",
            source_table="TEST",
        )

    def test_mixed_promise_is_not_fulfilled_while_external_relation_is_unresolved(
        self,
    ):
        evaluate_related_promises(
            self.document,
            performed_by=None,
        )

        self.promise.refresh_from_db()

        self.assertEqual(
            self.promise.status,
            PaymentPromise.Status.ACTIVE,
        )

        self.assertFalse(
            self.promise.payment_confirmed
        )

        self.assertEqual(
            self.promise.promise_documents.count(),
            2,
        )

        self.assertTrue(
            self.promise.promise_documents.filter(
                document=self.document,
            ).exists()
        )

        self.assertTrue(
            self.promise.promise_documents.filter(
                document__isnull=True,
                source="Fact_Vta_Sin_Vencer",
                source_document_number="400001",
            ).exists()
        )
