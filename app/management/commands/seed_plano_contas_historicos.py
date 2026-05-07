import uuid
from importlib import import_module

from django.core.management.base import BaseCommand

from app.models import Escritorio, HistoricoContabil, PlanoContas


class Command(BaseCommand):
    help = "Popula plano de contas e historicos contabeis para o primeiro escritorio, se ainda nao existirem."

    def handle(self, *args, **options):
        seed_module = import_module("app.migrations.0014_seed_plano_contas_historicos")
        escritorio = Escritorio.objects.first()

        if not escritorio:
            self.stdout.write(self.style.WARNING("Nenhum Escritorio encontrado. Seed ignorado."))
            return

        plano_criado = 0
        historico_criado = 0

        if not PlanoContas.objects.filter(escritorio=escritorio).exists():
            PlanoContas.objects.bulk_create(
                [
                    PlanoContas(
                        id=uuid.uuid4(),
                        escritorio=escritorio,
                        codigo=codigo,
                        classificacao=classificacao,
                        nome=nome,
                        tipo=tipo,
                        natureza=natureza,
                        ativo=True,
                    )
                    for codigo, classificacao, nome, tipo, natureza in seed_module.PLANO_CONTAS
                ]
            )
            plano_criado = len(seed_module.PLANO_CONTAS)

        if not HistoricoContabil.objects.filter(escritorio=escritorio).exists():
            HistoricoContabil.objects.bulk_create(
                [
                    HistoricoContabil(
                        id=uuid.uuid4(),
                        escritorio=escritorio,
                        codigo=codigo,
                        nome=nome,
                        grupo=grupo,
                        ativo=True,
                    )
                    for codigo, nome, grupo in seed_module.HISTORICOS
                ]
            )
            historico_criado = len(seed_module.HISTORICOS)

        if not plano_criado and not historico_criado:
            self.stdout.write(self.style.SUCCESS("Plano de contas e historicos ja existentes. Nada a fazer."))
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed concluido. PlanoContas criados: {plano_criado}. Historicos criados: {historico_criado}."
            )
        )
