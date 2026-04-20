from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0003_uuid_ossp_defaults"),
    ]

    operations = [
        migrations.RunSQL(
            sql='ALTER TABLE "app_cliente" ALTER COLUMN "codigo" SET DEFAULT (\'CLI-\' || upper(substr(replace(uuid_generate_v4()::text, \'-\', \'\'), 1, 8)));',
            reverse_sql='ALTER TABLE "app_cliente" ALTER COLUMN "codigo" DROP DEFAULT;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "app_cliente" ALTER COLUMN "criado_em" SET DEFAULT CURRENT_TIMESTAMP;',
            reverse_sql='ALTER TABLE "app_cliente" ALTER COLUMN "criado_em" DROP DEFAULT;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "app_cliente" ALTER COLUMN "atualizado_em" SET DEFAULT CURRENT_TIMESTAMP;',
            reverse_sql='ALTER TABLE "app_cliente" ALTER COLUMN "atualizado_em" DROP DEFAULT;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "app_keycloakuser" ALTER COLUMN "criado_em" SET DEFAULT CURRENT_TIMESTAMP;',
            reverse_sql='ALTER TABLE "app_keycloakuser" ALTER COLUMN "criado_em" DROP DEFAULT;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "app_keycloakuser" ALTER COLUMN "atualizado_em" SET DEFAULT CURRENT_TIMESTAMP;',
            reverse_sql='ALTER TABLE "app_keycloakuser" ALTER COLUMN "atualizado_em" DROP DEFAULT;',
        ),
    ]
