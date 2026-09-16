from decimal import Decimal

from django.test import TestCase

from apps.portfolio.models import (
    Customer,
    CustomerSAPProfile,
)
from apps.portfolio.services.customer_credit import (
    CustomerCreditService,
)


class CustomerCreditServiceTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            external_id="76147029-C",
            rut="76147029-9",
            name=(
                "SOCIEDAD CONSTRUCTORA "
                "IVESA LIMITADA"
            ),
        )

    def create_profile(
        self,
        **overrides,
    ):
        values = {
            "account_balance":
                Decimal("9247900"),
            "sales_order_balance":
                Decimal("0"),
            "delivery_note_balance":
                Decimal("0"),
            "credit_limit":
                Decimal("56000000"),
        }

        values.update(overrides)

        return CustomerSAPProfile.objects.create(
            customer=self.customer,
            **values,
        )

    def test_available_credit_formula(self):
        profile = self.create_profile()

        result = (
            CustomerCreditService
            .calculate(profile)
        )

        self.assertTrue(
            result.has_credit_line
        )

        self.assertEqual(
            result.used_amount,
            Decimal("9247900"),
        )

        self.assertEqual(
            result.available_amount,
            Decimal("46752100"),
        )

        self.assertEqual(
            result.status,
            CustomerCreditService
            .STATUS_AVAILABLE,
        )

    def test_negative_available_is_exceeded(self):
        profile = self.create_profile(
            account_balance=Decimal(
                "60000000"
            ),
        )

        result = (
            CustomerCreditService
            .calculate(profile)
        )

        self.assertEqual(
            result.available_amount,
            Decimal("-4000000"),
        )

        self.assertEqual(
            result.exceeded_amount,
            Decimal("4000000"),
        )

        self.assertEqual(
            result.status,
            CustomerCreditService
            .STATUS_EXCEEDED,
        )

    def test_zero_credit_limit_means_no_line(self):
        profile = self.create_profile(
            credit_limit=Decimal("0"),
        )

        result = (
            CustomerCreditService
            .calculate(profile)
        )

        self.assertFalse(
            result.has_credit_line
        )

        self.assertIsNone(
            result.available_amount
        )

        self.assertEqual(
            result.status,
            CustomerCreditService
            .STATUS_NO_LINE,
        )

    def test_null_credit_limit_means_no_line(self):
        profile = self.create_profile(
            credit_limit=None,
        )

        result = (
            CustomerCreditService
            .calculate(profile)
        )

        self.assertFalse(
            result.has_credit_line
        )

        self.assertEqual(
            result.status,
            CustomerCreditService
            .STATUS_NO_LINE,
        )

    def test_negative_component_is_preserved(self):
        profile = self.create_profile(
            sales_order_balance=Decimal(
                "-1000000"
            ),
        )

        result = (
            CustomerCreditService
            .calculate(profile)
        )

        self.assertEqual(
            result.used_amount,
            Decimal("8247900"),
        )

        self.assertEqual(
            result.available_amount,
            Decimal("47752100"),
        )

    def test_missing_component_is_incomplete(self):
        profile = self.create_profile(
            account_balance=None,
        )

        result = (
            CustomerCreditService
            .calculate(profile)
        )

        self.assertTrue(
            result.has_credit_line
        )

        self.assertFalse(
            result.is_complete
        )

        self.assertIsNone(
            result.available_amount
        )

        self.assertEqual(
            result.status,
            CustomerCreditService
            .STATUS_INCOMPLETE,
        )
