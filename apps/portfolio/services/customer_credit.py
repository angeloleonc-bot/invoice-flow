from dataclasses import dataclass
from decimal import Decimal


ZERO = Decimal("0")


@dataclass(frozen=True)
class CustomerCreditSnapshot:
    has_credit_line: bool
    is_complete: bool
    credit_limit: Decimal | None
    account_balance: Decimal | None
    sales_order_balance: Decimal | None
    delivery_note_balance: Decimal | None
    used_amount: Decimal | None
    available_amount: Decimal | None
    exceeded_amount: Decimal | None
    status: str


class CustomerCreditService:
    STATUS_NO_LINE = "NO_LINE"
    STATUS_INCOMPLETE = "INCOMPLETE"
    STATUS_AVAILABLE = "AVAILABLE"
    STATUS_EXCEEDED = "EXCEEDED"

    @staticmethod
    def _decimal(value):
        if value is None:
            return None

        if isinstance(value, Decimal):
            return value

        return Decimal(str(value))

    @classmethod
    def calculate(cls, profile):
        credit_limit = cls._decimal(
            profile.credit_limit
        )

        account_balance = cls._decimal(
            profile.account_balance
        )

        sales_order_balance = cls._decimal(
            profile.sales_order_balance
        )

        delivery_note_balance = cls._decimal(
            profile.delivery_note_balance
        )

        has_credit_line = (
            credit_limit is not None
            and credit_limit > ZERO
        )

        if not has_credit_line:
            return CustomerCreditSnapshot(
                has_credit_line=False,
                is_complete=True,
                credit_limit=credit_limit,
                account_balance=account_balance,
                sales_order_balance=sales_order_balance,
                delivery_note_balance=delivery_note_balance,
                used_amount=None,
                available_amount=None,
                exceeded_amount=None,
                status=cls.STATUS_NO_LINE,
            )

        components = (
            account_balance,
            sales_order_balance,
            delivery_note_balance,
        )

        if any(
            value is None
            for value in components
        ):
            return CustomerCreditSnapshot(
                has_credit_line=True,
                is_complete=False,
                credit_limit=credit_limit,
                account_balance=account_balance,
                sales_order_balance=sales_order_balance,
                delivery_note_balance=delivery_note_balance,
                used_amount=None,
                available_amount=None,
                exceeded_amount=None,
                status=cls.STATUS_INCOMPLETE,
            )

        used_amount = (
            account_balance
            + sales_order_balance
            + delivery_note_balance
        )

        available_amount = (
            credit_limit
            - used_amount
        )

        exceeded_amount = (
            abs(available_amount)
            if available_amount < ZERO
            else ZERO
        )

        status = (
            cls.STATUS_EXCEEDED
            if available_amount < ZERO
            else cls.STATUS_AVAILABLE
        )

        return CustomerCreditSnapshot(
            has_credit_line=True,
            is_complete=True,
            credit_limit=credit_limit,
            account_balance=account_balance,
            sales_order_balance=sales_order_balance,
            delivery_note_balance=delivery_note_balance,
            used_amount=used_amount,
            available_amount=available_amount,
            exceeded_amount=exceeded_amount,
            status=status,
        )
