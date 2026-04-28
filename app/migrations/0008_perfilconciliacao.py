import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0007_importacaoextrato_regraconciliador_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='PerfilConciliacao',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('nome', models.CharField(max_length=255)),
                ('descricao', models.TextField(blank=True, default='')),
                ('conta_bancaria', models.CharField(blank=True, default='', max_length=120)),
                ('codigo_historico', models.CharField(blank=True, default='', max_length=120)),
                ('codigo_empresa', models.CharField(blank=True, default='', max_length=120)),
                ('cnpj', models.CharField(blank=True, default='', max_length=20)),
                ('parametros', models.JSONField(blank=True, default=list)),
                ('ativo', models.BooleanField(db_index=True, default=True)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
                ('empresa', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='perfis_conciliacao', to='app.cliente')),
                ('escritorio', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='perfis_conciliacao', to='app.escritorio')),
            ],
            options={
                'ordering': ['nome'],
                'indexes': [
                    models.Index(fields=['escritorio', 'ativo'], name='app_perfilc_escritor_idx'),
                    models.Index(fields=['empresa', 'ativo'], name='app_perfilc_empresa_idx'),
                ],
            },
        ),
    ]
