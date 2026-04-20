from django.contrib import admin

from .models import Cliente, Escritorio, ImportacaoExtrato, KeycloakUser, RegraConciliador, TransacaoImportada


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nome", "cpf_cnpj", "ie", "telefone", "data_inicio", "situacao")
    list_filter = ("situacao", "data_inicio")
    search_fields = ("codigo", "nome", "cpf_cnpj", "ie", "telefone")
    ordering = ("nome",)


@admin.register(Escritorio)
class EscritorioAdmin(admin.ModelAdmin):
    list_display = ("nome", "cnpj", "criado_em", "atualizado_em")
    search_fields = ("nome", "cnpj")
    ordering = ("nome",)


@admin.register(ImportacaoExtrato)
class ImportacaoExtratoAdmin(admin.ModelAdmin):
    list_display = ("escritorio", "empresa", "referencia", "tipo_arquivo", "status", "criado_em")
    list_filter = ("tipo_arquivo", "status", "referencia", "escritorio")
    search_fields = ("escritorio__nome", "empresa__nome", "referencia")
    ordering = ("-criado_em",)


@admin.register(RegraConciliador)
class RegraConciliadorAdmin(admin.ModelAdmin):
    list_display = ("nome", "escritorio", "empresa", "tipo_movimento", "tipo_comparacao", "prioridade", "ativo")
    list_filter = ("escritorio", "tipo_movimento", "tipo_comparacao", "ativo")
    search_fields = ("nome", "texto_referencia", "categoria", "subcategoria", "codigo_historico")
    ordering = ("prioridade", "nome")


@admin.register(TransacaoImportada)
class TransacaoImportadaAdmin(admin.ModelAdmin):
    list_display = ("data_movimento", "descricao_original", "valor", "tipo_movimento", "regra_aplicada", "revisado_manual")
    list_filter = ("tipo_movimento", "revisado_manual", "data_movimento")
    search_fields = ("descricao_original", "descricao_normalizada", "categoria", "codigo_historico")
    ordering = ("-data_movimento",)


@admin.register(KeycloakUser)
class KeycloakUserAdmin(admin.ModelAdmin):
    list_display = ("nome", "email", "sub", "last_seen_at")
    search_fields = ("nome", "email", "sub")
    ordering = ("nome", "email")
