from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from django.utils import timezone
from rest_framework import serializers

from .models import Cliente, Escritorio, ImportacaoExtrato, PerfilConciliacao, RegraConciliador, TransacaoImportada, TipoArquivo, TipoComparacao, TipoMovimento


def _normalize_situacao(value: str) -> str:
    normalized = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")

    aliases = {
        "ATIVO": Cliente.Situacao.ATIVO,
        "ACTIVE": Cliente.Situacao.ATIVO,
        "EM_ANALISE": Cliente.Situacao.EM_ANALISE,
        "EMANALISE": Cliente.Situacao.EM_ANALISE,
        "ANALISE": Cliente.Situacao.EM_ANALISE,
        "PAUSADO": Cliente.Situacao.PAUSADO,
        "INATIVO": Cliente.Situacao.INATIVO,
    }

    if normalized not in aliases:
        raise serializers.ValidationError("Situação inválida.")

    return aliases[normalized]


def _formatar_cpf_cnpj(cpf_cnpj: str) -> str:
    digits = "".join(filter(str.isdigit, cpf_cnpj or ""))
    if len(digits) == 14:
        return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:14]}"
    elif len(digits) == 11:
        return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:11]}"
    return cpf_cnpj or ""


def _strip_text(value) -> str:
    return str(value or "").strip()


def _format_currency(value) -> str:
    amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    formatted = f"{amount:,.2f}"
    return f"R$ {formatted.replace(',', 'X').replace('.', ',').replace('X', '.')}"


class ClienteSerializer(serializers.ModelSerializer):
    situacao_label = serializers.CharField(source="get_situacao_display", read_only=True)
    cpf_cnpj_formatado = serializers.SerializerMethodField()

    class Meta:
        model = Cliente
        fields = [
            "id",
            "codigo",
            "nome",
            "cpf_cnpj",
            "cpf_cnpj_formatado",
            "ie",
            "telefone",
            "conta_corrente",
            "conta_contabil",
            "data_inicio",
            "situacao",
            "situacao_label",
            "criado_em",
            "atualizado_em",
        ]
        read_only_fields = ["id", "situacao_label", "criado_em", "atualizado_em"]
        extra_kwargs = {
            "codigo": {"required": False, "allow_blank": True},
            "ie": {"required": False, "allow_blank": True},
            "telefone": {"required": False, "allow_blank": True},
            "conta_corrente": {"required": False, "allow_blank": True},
            "conta_contabil": {"required": False, "allow_blank": True},
            "data_inicio": {"required": False},
        }

    def validate_codigo(self, value):
        return str(value or "").strip().upper()

    def validate_nome(self, value):
        return str(value or "").strip()

    def validate_cpf_cnpj(self, value):
        return str(value or "").strip()

    def validate_ie(self, value):
        return str(value or "").strip()

    def validate_telefone(self, value):
        return str(value or "").strip()

    def validate_conta_corrente(self, value):
        return str(value or "").strip()

    def validate_conta_contabil(self, value):
        return str(value or "").strip()

    def validate_situacao(self, value):
        return _normalize_situacao(value)

    def get_cpf_cnpj_formatado(self, obj) -> str:
        cpf_cnpj = obj.cpf_cnpj or ""
        digits = "".join(filter(str.isdigit, cpf_cnpj))
        if len(digits) == 14:
            return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:14]}"
        elif len(digits) == 11:
            return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:11]}"
        return cpf_cnpj

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not attrs.get("data_inicio"):
            attrs["data_inicio"] = timezone.localdate()
        return attrs


class EscritorioSerializer(serializers.ModelSerializer):
    class Meta:
        model = Escritorio
        fields = ["id", "nome", "cnpj", "criado_em", "atualizado_em"]
        read_only_fields = ["id", "criado_em", "atualizado_em"]

    def validate_nome(self, value):
        return str(value or "").strip()

    def validate_cnpj(self, value):
        return str(value or "").strip()


class RegraConciliadorSerializer(serializers.ModelSerializer):
    escritorio_nome = serializers.CharField(source="escritorio.nome", read_only=True)
    empresa_nome = serializers.CharField(source="empresa.nome", read_only=True)
    tipo_comparacao_label = serializers.CharField(source="get_tipo_comparacao_display", read_only=True)
    tipo_movimento_label = serializers.CharField(source="get_tipo_movimento_display", read_only=True)

    class Meta:
        model = RegraConciliador
        fields = [
            "id",
            "escritorio",
            "escritorio_nome",
            "empresa",
            "empresa_nome",
            "nome",
            "texto_referencia",
            "tipo_comparacao",
            "tipo_comparacao_label",
            "tipo_movimento",
            "tipo_movimento_label",
            "categoria",
            "subcategoria",
            "codigo_historico",
            "conta_debito",
            "conta_credito",
            "aplicar_automatico",
            "prioridade",
            "ativo",
            "criado_em",
            "atualizado_em",
        ]
        read_only_fields = ["id", "escritorio_nome", "empresa_nome", "tipo_comparacao_label", "tipo_movimento_label", "criado_em", "atualizado_em"]

    def validate_nome(self, value):
        return _strip_text(value)

    def validate_texto_referencia(self, value):
        return _strip_text(value)

    def validate_categoria(self, value):
        return _strip_text(value)

    def validate_subcategoria(self, value):
        return _strip_text(value)

    def validate_codigo_historico(self, value):
        return _strip_text(value)

    def validate_conta_debito(self, value):
        return _strip_text(value)

    def validate_conta_credito(self, value):
        return _strip_text(value)

    def validate_tipo_comparacao(self, value):
        return value or TipoComparacao.CONTEM

    def validate_tipo_movimento(self, value):
        return value or TipoMovimento.AMBOS


class ImportacaoExtratoSerializer(serializers.ModelSerializer):
    escritorio_nome = serializers.CharField(source="escritorio.nome", read_only=True)
    empresa_nome = serializers.CharField(source="empresa.nome", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    arquivo_url = serializers.SerializerMethodField()
    arquivo_nome = serializers.SerializerMethodField()
    transacoes_total = serializers.SerializerMethodField()
    transacoes_aplicadas = serializers.SerializerMethodField()
    transacoes_pendentes = serializers.SerializerMethodField()

    class Meta:
        model = ImportacaoExtrato
        fields = [
            "id",
            "escritorio",
            "escritorio_nome",
            "empresa",
            "empresa_nome",
            "referencia",
            "conta_bancaria",
            "arquivo",
            "arquivo_url",
            "arquivo_nome",
            "tipo_arquivo",
            "status",
            "status_label",
            "configuracao",
            "metadados",
            "mensagem_erro",
            "transacoes_total",
            "transacoes_aplicadas",
            "transacoes_pendentes",
            "criado_em",
            "atualizado_em",
        ]
        read_only_fields = [
            "id",
            "escritorio_nome",
            "empresa_nome",
            "arquivo_url",
            "arquivo_nome",
            "tipo_arquivo",
            "status",
            "status_label",
            "mensagem_erro",
            "transacoes_total",
            "transacoes_aplicadas",
            "transacoes_pendentes",
            "criado_em",
            "atualizado_em",
        ]

    def validate_referencia(self, value):
        value = _strip_text(value)
        if value and len(value) != 7:
            raise serializers.ValidationError("A referência deve usar o formato YYYY-MM.")
        return value

    def validate_conta_bancaria(self, value):
        return _strip_text(value)

    def validate_configuracao(self, value):
        return value if isinstance(value, dict) else {}

    def validate_arquivo(self, value):
        filename = Path(getattr(value, "name", "arquivo")).name.lower()
        if not any(filename.endswith(ext) for ext in (".csv", ".xls", ".xlsx", ".pdf")):
            raise serializers.ValidationError("Arquivo inválido. Use CSV, XLS, XLSX ou PDF.")
        return value

    def get_arquivo_url(self, obj):
        if not obj.arquivo:
            return ""
        request = self.context.get("request")
        if request is not None:
            return request.build_absolute_uri(obj.arquivo.url)
        return obj.arquivo.url

    def get_arquivo_nome(self, obj):
        if not obj.arquivo:
            return ""
        return Path(obj.arquivo.name).name

    def get_transacoes_total(self, obj):
        return obj.transacoes.count()

    def get_transacoes_aplicadas(self, obj):
        return obj.transacoes.filter(regra_aplicada__isnull=False).count()

    def get_transacoes_pendentes(self, obj):
        return obj.transacoes.filter(regra_aplicada__isnull=True, revisado_manual=False).count()


class TransacaoImportadaSerializer(serializers.ModelSerializer):
    importacao_status = serializers.CharField(source="importacao.status", read_only=True)
    regra_aplicada_nome = serializers.CharField(source="regra_aplicada.nome", read_only=True)
    regra_aplicada_texto = serializers.CharField(source="regra_aplicada.texto_referencia", read_only=True)
    tipo_movimento_label = serializers.CharField(source="get_tipo_movimento_display", read_only=True)
    valor_formatado = serializers.SerializerMethodField()
    debito = serializers.SerializerMethodField()
    credito = serializers.SerializerMethodField()
    historico_final = serializers.SerializerMethodField()
    status_aplicacao = serializers.SerializerMethodField()

    class Meta:
        model = TransacaoImportada
        fields = [
            "id",
            "importacao",
            "importacao_status",
            "linha_origem",
            "data_movimento",
            "descricao_original",
            "descricao_normalizada",
            "valor",
            "valor_formatado",
            "tipo_movimento",
            "tipo_movimento_label",
            "regra_aplicada",
            "regra_aplicada_nome",
            "regra_aplicada_texto",
            "categoria",
            "subcategoria",
            "conta_debito",
            "conta_credito",
            "codigo_historico",
            "revisado_manual",
            "dados_brutos",
            "debito",
            "credito",
            "historico_final",
            "status_aplicacao",
            "criado_em",
            "atualizado_em",
        ]
        read_only_fields = [
            "id",
            "importacao",
            "importacao_status",
            "linha_origem",
            "descricao_original",
            "valor_formatado",
            "tipo_movimento_label",
            "regra_aplicada_nome",
            "regra_aplicada_texto",
            "debito",
            "credito",
            "historico_final",
            "status_aplicacao",
            "criado_em",
            "atualizado_em",
        ]
        extra_kwargs = {
            "descricao_normalizada": {"required": False, "allow_blank": True},
            "categoria": {"required": False, "allow_blank": True},
            "subcategoria": {"required": False, "allow_blank": True},
            "conta_debito": {"required": False, "allow_blank": True},
            "conta_credito": {"required": False, "allow_blank": True},
            "codigo_historico": {"required": False, "allow_blank": True},
            "revisado_manual": {"required": False},
            "regra_aplicada": {"required": False, "allow_null": True},
        }

    def validate_descricao_normalizada(self, value):
        return _strip_text(value)

    def validate_categoria(self, value):
        return _strip_text(value)

    def validate_subcategoria(self, value):
        return _strip_text(value)

    def validate_conta_debito(self, value):
        return _strip_text(value)

    def validate_conta_credito(self, value):
        return _strip_text(value)

    def validate_codigo_historico(self, value):
        return _strip_text(value)

    def validate_dados_brutos(self, value):
        return value if isinstance(value, dict) else {}

    def get_valor_formatado(self, obj):
        return _format_currency(obj.valor)

    def get_debito(self, obj):
        return _format_currency(obj.valor) if obj.tipo_movimento == TipoMovimento.DEBITO else ""

    def get_credito(self, obj):
        return _format_currency(obj.valor) if obj.tipo_movimento == TipoMovimento.CREDITO else ""

    def get_historico_final(self, obj):
        return obj.codigo_historico or obj.categoria or obj.descricao_normalizada or obj.descricao_original or "PENDENTE"

    def get_status_aplicacao(self, obj):
        if obj.revisado_manual:
            return "MANUAL"
        return "APLICADO" if obj.regra_aplicada_id else "PENDENTE"


class PerfilConciliacaoSerializer(serializers.ModelSerializer):
    escritorio_nome = serializers.CharField(source="escritorio.nome", read_only=True)
    empresa_nome = serializers.CharField(source="empresa.nome", read_only=True)
    parametros_count = serializers.SerializerMethodField()

    class Meta:
        model = PerfilConciliacao
        fields = [
            "id",
            "escritorio",
            "escritorio_nome",
            "empresa",
            "empresa_nome",
            "nome",
            "descricao",
            "conta_bancaria",
            "codigo_historico",
            "codigo_empresa",
            "cnpj",
            "parametros",
            "parametros_count",
            "ativo",
            "criado_em",
            "atualizado_em",
        ]
        read_only_fields = ["id", "escritorio_nome", "empresa_nome", "parametros_count", "criado_em", "atualizado_em"]
        extra_kwargs = {
            "descricao": {"required": False, "allow_blank": True},
            "conta_bancaria": {"required": False, "allow_blank": True},
            "codigo_historico": {"required": False, "allow_blank": True},
            "codigo_empresa": {"required": False, "allow_blank": True},
            "cnpj": {"required": False, "allow_blank": True},
            "parametros": {"required": False},
        }

    def validate_nome(self, value):
        return _strip_text(value)

    def validate_descricao(self, value):
        return _strip_text(value)

    def validate_conta_bancaria(self, value):
        return _strip_text(value)

    def validate_codigo_historico(self, value):
        return _strip_text(value)

    def validate_codigo_empresa(self, value):
        return _strip_text(value)

    def validate_cnpj(self, value):
        return _strip_text(value)

    def validate_parametros(self, value):
        return value if isinstance(value, list) else []

    def get_parametros_count(self, obj):
        params = obj.parametros or []
        return len(params) if isinstance(params, list) else 0

