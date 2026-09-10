from __future__ import annotations

from decimal import Decimal

from django.db.models import QuerySet

from apps.management.models import CollectionAction
from apps.portfolio.models import Document


class OperationalPortfolioService:
    """
    Reglas transversales del universo operacional de cobranza.

    Principios:

    - una deuda puede seguir existiendo financieramente sin requerir una alerta;
    - My Work debe conservar toda la cartera cobrable;
    - la prioridad ordena/destaca, no excluye;
    - eventos técnicos del motor de alertas no cuentan como gestión comercial.

    Esta clase no modifica datos.
    """

    COLLECTION_ACTION_TYPES = (
        CollectionAction.ActionType.CALL,
        CollectionAction.ActionType.EMAIL,
        CollectionAction.ActionType.WHATSAPP,
        CollectionAction.ActionType.NOTE,
        CollectionAction.ActionType.PROMISE,
        CollectionAction.ActionType.PAYMENT_INFO,
    )

    SYSTEM_ACTION_TYPES = (
        CollectionAction.ActionType.ALERT_RESOLVED,
        CollectionAction.ActionType.ALERT_POSTPONED,
        CollectionAction.ActionType.ALERT_REOPENED,
    )

    @classmethod
    def operational_documents(cls) -> QuerySet:
        """
        Universo financiero que puede formar parte de la cobranza operativa.

        Por ahora se conserva exactamente la regla ya usada por My Work:
        saldo pendiente estrictamente mayor que cero.

        No se aplica antigüedad como criterio de exclusión.
        """
        return Document.objects.filter(
            balance_amount__gt=Decimal("0"),
        )

    @classmethod
    def is_operational_document(cls, document: Document) -> bool:
        """
        Evaluación equivalente para una instancia ya cargada.
        """
        if document is None:
            return False

        balance = getattr(
            document,
            "balance_amount",
            Decimal("0"),
        )

        return balance is not None and balance > Decimal("0")

    @classmethod
    def collection_actions(cls) -> QuerySet:
        """
        Gestiones que representan actividad real de cobranza.

        Se excluyen explícitamente los eventos técnicos de OperationalAlert.
        """
        return CollectionAction.objects.filter(
            action_type__in=cls.COLLECTION_ACTION_TYPES,
        )

    @classmethod
    def collection_actions_for_document(
        cls,
        document,
    ) -> QuerySet:
        return cls.collection_actions().filter(
            document=document,
        )

    @classmethod
    def collection_actions_for_customer(
        cls,
        customer,
    ) -> QuerySet:
        return cls.collection_actions().filter(
            customer=customer,
        )

    @classmethod
    def has_collection_management_since(
        cls,
        *,
        document=None,
        customer=None,
        since,
    ) -> bool:
        """
        Devuelve True sólo si existe gestión comercial real desde ``since``.

        Debe informarse exactamente document o customer.
        """
        if (document is None) == (customer is None):
            raise ValueError(
                "Debe informarse exactamente document o customer."
            )

        queryset = cls.collection_actions().filter(
            action_date__gte=since,
        )

        if document is not None:
            queryset = queryset.filter(
                document=document,
            )
        else:
            queryset = queryset.filter(
                customer=customer,
            )

        return queryset.exists()
