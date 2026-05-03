from django.contrib import admin

from .models import (
    Banco,
    CertificadoDigitalCliente,
    Cliente,
    ContaCliente,
    Escritorio,
    ImportacaoExtrato,
    KeycloakUser,
    LancamentoComponente,
    RegraConciliador,
    TarifaVinculoAuditoria,
    TransacaoImportada,
)


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nome", "cpf_cnpj", "ie", "telefone", "data_inicio", "situacao")
    list_filter = ("situacao", "data_inicio")
    search_fields = ("codigo", "nome", "cpf_cnpj", "ie", "telefone")
    ordering = ("nome",)


@admin.register(ContaCliente)
class ContaClienteAdmin(admin.ModelAdmin):
    list_display = ("cliente", "tipo", "apelido", "ativo", "criado_em")
    list_filter = ("tipo", "ativo", "cliente")
    search_fields = ("apelido", "banco", "agencia", "numero", "codigo_contabil", "descricao_contabil", "cliente__nome")
    ordering = ("-ativo", "tipo", "apelido")


@admin.register(CertificadoDigitalCliente)
class CertificadoDigitalClienteAdmin(admin.ModelAdmin):
    list_display = ("cliente", "tipo_arquivo", "ativo", "tamanho_bytes", "criado_em")
    list_filter = ("tipo_arquivo", "ativo", "cliente")
    search_fields = ("cliente__nome", "arquivo_original", "tipo_arquivo")
    ordering = ("cliente__nome",)


@admin.register(Escritorio)
class EscritorioAdmin(admin.ModelAdmin):
    list_display = ("nome", "cnpj", "criado_em", "atualizado_em")
    search_fields = ("nome", "cnpj")
    ordering = ("nome",)


@admin.register(Banco)
class BancoAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nome", "sigla", "ativo", "atualizado_em")
    list_filter = ("ativo",)
    search_fields = ("codigo", "nome", "slug", "sigla")
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
    list_display = (
        "data_movimento",
        "descricao_original",
        "valor",
        "tipo_movimento",
        "tipo_lancamento",
        "status_vinculo_tarifa",
        "regra_aplicada",
        "revisado_manual",
    )
    list_filter = ("tipo_movimento", "tipo_lancamento", "status_vinculo_tarifa", "revisado_manual", "data_movimento")
    search_fields = ("descricao_original", "descricao_normalizada", "categoria", "codigo_historico")
    ordering = ("-data_movimento",)


@admin.register(LancamentoComponente)
class LancamentoComponenteAdmin(admin.ModelAdmin):
    list_display = ("lancamento", "tipo_componente", "valor", "descricao", "atualizado_em")
    list_filter = ("tipo_componente",)
    search_fields = ("descricao", "lancamento__descricao_original")
    ordering = ("lancamento", "tipo_componente")


@admin.register(TarifaVinculoAuditoria)
class TarifaVinculoAuditoriaAdmin(admin.ModelAdmin):
    list_display = ("lancamento_principal", "lancamento_tarifa", "origem", "usuario", "status_anterior", "status_novo", "criado_em")
    list_filter = ("origem", "status_anterior", "status_novo", "criado_em")
    search_fields = ("usuario", "lancamento_principal__descricao_original", "lancamento_tarifa__descricao_original")
    ordering = ("-criado_em",)


@admin.register(KeycloakUser)
class KeycloakUserAdmin(admin.ModelAdmin):
    list_display = ("nome", "email", "sub", "last_seen_at")
    search_fields = ("nome", "email", "sub")
    ordering = ("nome", "email")
