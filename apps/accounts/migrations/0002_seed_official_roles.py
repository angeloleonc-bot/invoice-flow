from django.db import migrations


OFFICIAL_ROLES = [
    {
        "code": "ADMINISTRADOR",
        "name": "Administrador",
        "description": (
            "Acceso administrativo completo a Invoice Flow, incluyendo "
            "configuración, supervisión y funciones operacionales."
        ),
    },
    {
        "code": "SUPERVISOR",
        "name": "Supervisor",
        "description": (
            "Acceso supervisor a la cartera, alertas operacionales, "
            "asignaciones y seguimiento del equipo de cobranza."
        ),
    },
    {
        "code": "COBRADOR",
        "name": "Cobrador",
        "description": (
            "Acceso operacional limitado a clientes, documentos y alertas "
            "asignados al usuario."
        ),
    },
    {
        "code": "CONSULTA_AUDITORIA",
        "name": "Consulta / Auditoría",
        "description": (
            "Acceso de consulta y auditoría con visibilidad amplia, "
            "sin atribuciones operacionales administrativas."
        ),
    },
]


def seed_official_roles(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")

    for role_data in OFFICIAL_ROLES:
        Role.objects.update_or_create(
            code=role_data["code"],
            defaults={
                "name": role_data["name"],
                "description": role_data["description"],
                "is_active": True,
            },
        )


def reverse_seed_official_roles(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")

    Role.objects.filter(
        code__in=[
            role["code"]
            for role in OFFICIAL_ROLES
        ]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            seed_official_roles,
            reverse_seed_official_roles,
        ),
    ]