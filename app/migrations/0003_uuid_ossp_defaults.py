from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0002_seed_clientes"),
    ]

    operations = [
        migrations.RunSQL(
            sql='CREATE EXTENSION IF NOT EXISTS "uuid-ossp";',
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "app_cliente" ALTER COLUMN "id" SET DEFAULT uuid_generate_v4();',
            reverse_sql='ALTER TABLE "app_cliente" ALTER COLUMN "id" DROP DEFAULT;',
        ),
        migrations.RunSQL(
            sql='ALTER TABLE "app_keycloakuser" ALTER COLUMN "id" SET DEFAULT uuid_generate_v4();',
            reverse_sql='ALTER TABLE "app_keycloakuser" ALTER COLUMN "id" DROP DEFAULT;',
        ),
    ]
