import app.models
from django.db import migrations, models
import uuid


BANCOS_INICIAIS = [
    {"codigo": "001", "nome": "Banco do Brasil", "slug": "banco-do-brasil", "sigla": "BB", "cor_principal": "#1746a2"},
    {"codigo": "003", "nome": "Banco da Amazônia", "slug": "banco-da-amazonia", "sigla": "BASA", "cor_principal": "#15803d"},
    {"codigo": "237", "nome": "Bradesco", "slug": "bradesco", "sigla": "BR", "cor_principal": "#dc2626"},
    {"codigo": "104", "nome": "Caixa", "slug": "caixa", "sigla": "CX", "cor_principal": "#005ca9"},
    {"codigo": "033", "nome": "Santander", "slug": "santander", "sigla": "ST", "cor_principal": "#e1251b"},
    {"codigo": "341", "nome": "Itaú", "slug": "itau", "sigla": "IT", "cor_principal": "#f97316"},
    {"codigo": "077", "nome": "Banco Inter", "slug": "banco-inter", "sigla": "IN", "cor_principal": "#ff7a00"},
    {"codigo": "260", "nome": "Nubank", "slug": "nubank", "sigla": "NU", "cor_principal": "#820ad1"},
    {"codigo": "748", "nome": "Sicredi", "slug": "sicredi", "sigla": "SI", "cor_principal": "#16a34a"},
    {"codigo": "756", "nome": "Sicoob", "slug": "sicoob", "sigla": "SC", "cor_principal": "#047857"},
    {"codigo": "208", "nome": "BTG Pactual", "slug": "btg-pactual", "sigla": "BT", "cor_principal": "#111827"},
    {"codigo": "422", "nome": "Safra", "slug": "safra", "sigla": "SF", "cor_principal": "#1d4ed8"},
    {"codigo": "041", "nome": "Banrisul", "slug": "banrisul", "sigla": "BN", "cor_principal": "#1d4ed8"},
    {"codigo": "336", "nome": "C6 Bank", "slug": "c6-bank", "sigla": "C6", "cor_principal": "#111827"},
    {"codigo": "212", "nome": "Banco Original", "slug": "banco-original", "sigla": "OR", "cor_principal": "#16a34a"},
    {"codigo": "004", "nome": "Banco do Nordeste", "slug": "banco-do-nordeste", "sigla": "NE", "cor_principal": "#f97316"},
    {"codigo": "389", "nome": "Mercantil do Brasil", "slug": "mercantil-do-brasil", "sigla": "MB", "cor_principal": "#dc2626"},
    {"codigo": "707", "nome": "Daycoval", "slug": "daycoval", "sigla": "DY", "cor_principal": "#1d4ed8"},
    {"codigo": "623", "nome": "Banco PAN", "slug": "banco-pan", "sigla": "PAN", "cor_principal": "#1d4ed8"},
    {"codigo": "413", "nome": "BV", "slug": "bv", "sigla": "BV", "cor_principal": "#4338ca"},
]


def seed_bancos(apps, _schema_editor):
    Banco = apps.get_model("app", "Banco")
    for item in BANCOS_INICIAIS:
        Banco.objects.update_or_create(
            codigo=item["codigo"],
            defaults={
                "nome": item["nome"],
                "slug": item["slug"],
                "sigla": item["sigla"],
                "cor_principal": item["cor_principal"],
                "ativo": True,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0014_seed_plano_contas_historicos"),
    ]

    operations = [
        migrations.CreateModel(
            name="Banco",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("codigo", models.CharField(db_index=True, max_length=10, unique=True)),
                ("nome", models.CharField(max_length=120, unique=True)),
                ("slug", models.SlugField(db_index=True, max_length=140, unique=True)),
                ("sigla", models.CharField(blank=True, default="", max_length=8)),
                ("cor_principal", models.CharField(blank=True, default="#64748b", max_length=20)),
                ("logo", models.FileField(blank=True, default="", max_length=255, upload_to=app.models._banco_logo_upload_to)),
                ("ativo", models.BooleanField(db_index=True, default=True)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["nome"],
            },
        ),
        migrations.AddIndex(
            model_name="banco",
            index=models.Index(fields=["ativo", "nome"], name="app_banco_ativo_aa2474_idx"),
        ),
        migrations.AddIndex(
            model_name="banco",
            index=models.Index(fields=["codigo"], name="app_banco_codigo_02e8fe_idx"),
        ),
        migrations.AddIndex(
            model_name="banco",
            index=models.Index(fields=["slug"], name="app_banco_slug_eb10cf_idx"),
        ),
        migrations.RunPython(seed_bancos, migrations.RunPython.noop),
    ]
