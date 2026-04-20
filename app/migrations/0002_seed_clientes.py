from __future__ import annotations

from datetime import date

from django.db import migrations


def seed_clientes(apps, schema_editor):
    Cliente = apps.get_model("app", "Cliente")

    if Cliente.objects.exists():
        return

    Cliente.objects.bulk_create(
        [
            Cliente(
                codigo="CLI-DEMO01",
                nome="Alfa Contabilidade Ltda",
                cpf_cnpj="12.345.678/0001-90",
                ie="123456789",
                telefone="(11) 3333-1010",
                data_inicio=date(2025, 1, 8),
                situacao="ATIVO",
            ),
            Cliente(
                codigo="CLI-DEMO02",
                nome="Grupo Solar ME",
                cpf_cnpj="98.765.432/0001-12",
                ie="",
                telefone="(11) 98888-2020",
                data_inicio=date(2025, 2, 14),
                situacao="EM_ANALISE",
            ),
            Cliente(
                codigo="CLI-DEMO03",
                nome="Norte Serviços Ltda",
                cpf_cnpj="45.678.901/0001-55",
                ie="55667788",
                telefone="(21) 3444-3030",
                data_inicio=date(2025, 3, 4),
                situacao="ATIVO",
            ),
            Cliente(
                codigo="CLI-DEMO04",
                nome="Delta Comércio e Representação",
                cpf_cnpj="23.456.789/0001-33",
                ie="99887766",
                telefone="(31) 97777-4040",
                data_inicio=date(2025, 3, 22),
                situacao="PAUSADO",
            ),
        ]
    )


def unseed_clientes(apps, schema_editor):
    Cliente = apps.get_model("app", "Cliente")
    Cliente.objects.filter(codigo__startswith="CLI-DEMO").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_clientes, reverse_code=unseed_clientes),
    ]
