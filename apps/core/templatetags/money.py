from decimal import Decimal, InvalidOperation

from django import template


register = template.Library()


@register.filter
def clp(value):
    """
    Formato monetario corporativo:
    $ 1.234.567
    Sin decimales.
    """
    if value is None or value == "":
        value = 0

    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        amount = Decimal("0")

    amount = int(round(amount, 0))

    formatted = f"{amount:,}".replace(",", ".")

    return f"$ {formatted}"