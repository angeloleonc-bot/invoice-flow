from django.db import migrations


def seed_official_catalogs(apps, schema_editor):
    DocumentStatus = apps.get_model("portfolio", "DocumentStatus")
    DocumentSubStatus = apps.get_model("portfolio", "DocumentSubStatus")
    DocumentTag = apps.get_model("portfolio", "DocumentTag")

    statuses = [
        "Sin asignar",
        "Asignada",
        "En gestión",
        "Pago programado",
        "Pagada",
        "Cerrada",
    ]

    substatuses = [
        "Contactado",
        "Sin contacto",
        "Requiere seguimiento",
        "Promesa pendiente de fecha",
        "Promesa vigente",
        "Promesa vencida",
        "Pago parcial informado",
        "Pago total informado",
    ]

    tags = [
        "Reclamada",
        "Refacturación",
        "Cliente crítico",
        "Marketplace",
        "No enviar",
        "Requiere supervisor",
        "Pago pendiente de aplicar",
        "Promesa vencida",
    ]

    for name in statuses:
        DocumentStatus.objects.get_or_create(name=name)

    for name in substatuses:
        DocumentSubStatus.objects.get_or_create(name=name)

    for name in tags:
        DocumentTag.objects.get_or_create(name=name)


class Migration(migrations.Migration):

    dependencies = [
        ("portfolio", "0003_paymentrecord"),
    ]

    operations = [
        migrations.RunPython(seed_official_catalogs, migrations.RunPython.noop),
    ]