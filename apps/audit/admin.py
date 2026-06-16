from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("event_type", "user", "username_snapshot", "ip_address", "created_at")
    search_fields = ("event_type", "username_snapshot", "user__username")
    list_filter = ("event_type", "created_at")
    readonly_fields = (
        "event_type",
        "user",
        "username_snapshot",
        "ip_address",
        "user_agent",
        "metadata",
        "created_at",
    )