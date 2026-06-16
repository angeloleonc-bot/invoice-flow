from apps.audit.models import AuditLog


class AuditService:
    """
    Servicio centralizado para registrar eventos de auditoría.

    En esta etapa no se conecta todavía con flujos reales de login,
    logout, sesiones ni proveedor externo.
    """

    @staticmethod
    def register_event(
        event_type,
        user=None,
        username_snapshot="",
        ip_address=None,
        user_agent="",
        metadata=None,
    ):
        return AuditLog.objects.create(
            event_type=event_type,
            user=user,
            username_snapshot=username_snapshot,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata=metadata or {},
        )