from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path

from django.utils import timezone
from rest_framework import serializers

from services.conciliador import normalize_text

from .models import (
    Banco,
    CertificadoDigitalCliente,
    Cliente,
    ConfiancaVinculo,
    ContaCliente,
    Escritorio,
    HistoricoContabil,
    ImportacaoExtrato,
    LancamentoComponente,
    PerfilConciliacao,
    PlanoContas,
    RegraConciliador,
    StatusVinculoTarifa,
    TarifaVinculoAuditoria,
    TransacaoImportada,
    TipoArquivo,
    TipoComparacao,
    TipoComponenteLancamento,
    TipoContaCliente,
    TipoLancamento,
    TipoMovimento,
)


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


def _field_value(attrs, instance, field_name: str) -> str:
    if field_name in attrs:
        return _strip_text(attrs.get(field_name))
    return _strip_text(getattr(instance, field_name, "") if instance is not None else "")


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
            "email",
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
            "email": {"required": False, "allow_blank": True},
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

    def validate_email(self, value):
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


class ContaClienteSerializer(serializers.ModelSerializer):
    cliente_nome = serializers.CharField(source="cliente.nome", read_only=True)
    tipo_label = serializers.CharField(source="get_tipo_display", read_only=True)
    resumo = serializers.SerializerMethodField()

    class Meta:
        model = ContaCliente
        fields = [
            "id",
            "cliente",
            "cliente_nome",
            "tipo",
            "tipo_label",
            "apelido",
            "banco",
            "agencia",
            "numero",
            "codigo_contabil",
            "descricao_contabil",
            "observacoes",
            "ativo",
            "resumo",
            "criado_em",
            "atualizado_em",
        ]
        read_only_fields = ["id", "cliente_nome", "tipo_label", "resumo", "criado_em", "atualizado_em"]
        extra_kwargs = {
            "cliente": {"required": False},
            "apelido": {"required": False, "allow_blank": True},
            "banco": {"required": False, "allow_blank": True},
            "agencia": {"required": False, "allow_blank": True},
            "numero": {"required": False, "allow_blank": True},
            "codigo_contabil": {"required": False, "allow_blank": True},
            "descricao_contabil": {"required": False, "allow_blank": True},
            "observacoes": {"required": False, "allow_blank": True},
        }

    def validate_tipo(self, value):
        return value or TipoContaCliente.BANCARIA

    def validate_apelido(self, value):
        return _strip_text(value)

    def validate_banco(self, value):
        return _strip_text(value)

    def validate_agencia(self, value):
        return _strip_text(value)

    def validate_numero(self, value):
        return _strip_text(value)

    def validate_codigo_contabil(self, value):
        return _strip_text(value)

    def validate_descricao_contabil(self, value):
        return _strip_text(value)

    def validate_observacoes(self, value):
        return _strip_text(value)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = getattr(self, "instance", None)
        tipo = _field_value(attrs, instance, "tipo") or TipoContaCliente.BANCARIA
        banco = _field_value(attrs, instance, "banco")
        agencia = _field_value(attrs, instance, "agencia")
        numero = _field_value(attrs, instance, "numero")
        codigo_contabil = _field_value(attrs, instance, "codigo_contabil")

        errors = {}

        if tipo == TipoContaCliente.BANCARIA:
            if not banco:
                errors["banco"] = "Informe o banco."
            if not agencia:
                errors["agencia"] = "Informe a agência."
            if not numero:
                errors["numero"] = "Informe o número da conta."
        elif tipo == TipoContaCliente.CONTABIL:
            if not codigo_contabil:
                errors["codigo_contabil"] = "Informe o código contábil."
        else:
            errors["tipo"] = "Tipo de conta inválido."

        if errors:
            raise serializers.ValidationError(errors)

        return attrs

    def get_resumo(self, obj):
        return obj.resumo()


class CertificadoDigitalClienteSerializer(serializers.ModelSerializer):
    cliente_nome = serializers.CharField(source="cliente.nome", read_only=True)
    tipo_arquivo_label = serializers.CharField(source="get_tipo_arquivo_display", read_only=True)
    tamanho_formatado = serializers.SerializerMethodField()
    resumo = serializers.SerializerMethodField()
    arquivo = serializers.FileField(write_only=True, required=False)

    class Meta:
        model = CertificadoDigitalCliente
        fields = [
            "id",
            "cliente",
            "cliente_nome",
            "arquivo",
            "arquivo_original",
            "tipo_arquivo",
            "tipo_arquivo_label",
            "tamanho_bytes",
            "tamanho_formatado",
            "hash_sha256",
            "ativo",
            "resumo",
            "criado_em",
            "atualizado_em",
        ]
        read_only_fields = [
            "id",
            "cliente_nome",
            "arquivo_original",
            "tipo_arquivo",
            "tipo_arquivo_label",
            "tamanho_bytes",
            "tamanho_formatado",
            "hash_sha256",
            "resumo",
            "criado_em",
            "atualizado_em",
        ]
        extra_kwargs = {
            "cliente": {"required": False},
            "ativo": {"required": False},
        }

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = getattr(self, "instance", None)
        arquivo = attrs.get("arquivo")
        cliente = attrs.get("cliente", getattr(instance, "cliente", None))

        if not cliente:
            raise serializers.ValidationError({"cliente": "Informe o cliente."})

        if instance is None and arquivo is None:
            raise serializers.ValidationError({"arquivo": "Envie um certificado digital."})

        return attrs

    def validate_arquivo(self, value):
        original_name = Path(getattr(value, "name", "certificado")).name
        suffix = Path(original_name).suffix.lower()
        if suffix not in {".pfx", ".p12"}:
            raise serializers.ValidationError("Use arquivos .pfx ou .p12.")

        data = value.read()
        value.seek(0)

        self._arquivo_original = original_name
        self._arquivo_tipo = "PFX" if suffix == ".pfx" else "P12"
        self._arquivo_tamanho = len(data)
        self._arquivo_hash = hashlib.sha256(data).hexdigest()
        return value

    def _apply_uploaded_file(self, instance, arquivo):
        if arquivo is None:
            return None

        old_name = instance.arquivo.name if instance and instance.pk and instance.arquivo else None
        instance.arquivo = arquivo
        instance.arquivo_original = getattr(self, "_arquivo_original", Path(getattr(arquivo, "name", "certificado")).name)
        instance.tipo_arquivo = getattr(self, "_arquivo_tipo", "PFX")
        instance.tamanho_bytes = getattr(self, "_arquivo_tamanho", getattr(arquivo, "size", 0) or 0)
        instance.hash_sha256 = getattr(self, "_arquivo_hash", "")
        return old_name

    def create(self, validated_data):
        arquivo = validated_data.pop("arquivo", None)
        instance = CertificadoDigitalCliente(**validated_data)
        old_name = self._apply_uploaded_file(instance, arquivo)
        instance.save()

        if old_name and old_name != instance.arquivo.name:
            instance.arquivo.storage.delete(old_name)

        return instance

    def update(self, instance, validated_data):
        arquivo = validated_data.pop("arquivo", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        old_name = self._apply_uploaded_file(instance, arquivo)
        instance.save()

        if old_name and old_name != instance.arquivo.name:
            instance.arquivo.storage.delete(old_name)

        return instance

    def get_tamanho_formatado(self, obj):
        size = int(obj.tamanho_bytes or 0)
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"

    def get_resumo(self, obj):
        return f"{obj.tipo_arquivo} · {self.get_tamanho_formatado(obj)}"


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


class LancamentoComponenteSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(required=False)
    tipo_componente_label = serializers.CharField(source="get_tipo_componente_display", read_only=True)
    valor_formatado = serializers.SerializerMethodField()

    class Meta:
        model = LancamentoComponente
        fields = [
            "id",
            "lancamento",
            "tipo_componente",
            "tipo_componente_label",
            "valor",
            "valor_formatado",
            "descricao",
            "criado_em",
            "atualizado_em",
        ]
        read_only_fields = ["lancamento", "tipo_componente_label", "valor_formatado", "criado_em", "atualizado_em"]

    def validate_tipo_componente(self, value):
        return value or TipoComponenteLancamento.PRINCIPAL

    def validate_descricao(self, value):
        return _strip_text(value)

    def get_valor_formatado(self, obj):
        return _format_currency(obj.valor)


class TarifaVinculoAuditoriaSerializer(serializers.ModelSerializer):
    class Meta:
        model = TarifaVinculoAuditoria
        fields = [
            "id",
            "lancamento_principal",
            "lancamento_tarifa",
            "usuario",
            "origem",
            "status_anterior",
            "status_novo",
            "criado_em",
        ]
        read_only_fields = fields


class TransacaoImportadaSerializer(serializers.ModelSerializer):
    importacao_status = serializers.CharField(source="importacao.status", read_only=True)
    regra_aplicada_nome = serializers.CharField(source="regra_aplicada.nome", read_only=True)
    regra_aplicada_texto = serializers.CharField(source="regra_aplicada.texto_referencia", read_only=True)
    tipo_movimento_label = serializers.CharField(source="get_tipo_movimento_display", read_only=True)
    tipo_lancamento_label = serializers.CharField(source="get_tipo_lancamento_display", read_only=True)
    status_vinculo_tarifa_label = serializers.CharField(source="get_status_vinculo_tarifa_display", read_only=True)
    confianca_vinculo_label = serializers.CharField(source="get_confianca_vinculo_display", read_only=True)
    valor_formatado = serializers.SerializerMethodField()
    debito = serializers.SerializerMethodField()
    credito = serializers.SerializerMethodField()
    historico_final = serializers.SerializerMethodField()
    status_aplicacao = serializers.SerializerMethodField()
    data_lancamento_extrato = serializers.DateField(source="data_movimento", read_only=True)
    descricao = serializers.CharField(source="descricao_original", read_only=True)
    componentes = LancamentoComponenteSerializer(many=True, required=False)
    tarifa_vinculada = serializers.SerializerMethodField()
    badge_tarifa = serializers.SerializerMethodField()
    composicao = serializers.SerializerMethodField()
    is_boleto = serializers.SerializerMethodField()
    is_pix_ted = serializers.SerializerMethodField()

    class Meta:
        model = TransacaoImportada
        fields = [
            "id",
            "importacao",
            "importacao_status",
            "linha_origem",
            "data_movimento",
            "data_lancamento_extrato",
            "data_ocorrencia",
            "descricao",
            "descricao_original",
            "descricao_normalizada",
            "valor",
            "valor_formatado",
            "tipo_movimento",
            "tipo_movimento_label",
            "tipo_lancamento",
            "tipo_lancamento_label",
            "lancamento_relacionado",
            "status_vinculo_tarifa",
            "status_vinculo_tarifa_label",
            "confianca_vinculo",
            "confianca_vinculo_label",
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
            "componentes",
            "tarifa_vinculada",
            "badge_tarifa",
            "composicao",
            "is_boleto",
            "is_pix_ted",
            "mostrar_botao_tarifa",
            "criado_em",
            "atualizado_em",
        ]
        read_only_fields = [
            "id",
            "importacao",
            "importacao_status",
            "linha_origem",
            "data_lancamento_extrato",
            "tarifa_vinculada",
            "badge_tarifa",
            "composicao",
            "is_boleto",
            "is_pix_ted",
            "mostrar_botao_tarifa",
            "descricao",
            "descricao_original",
            "valor_formatado",
            "tipo_movimento_label",
            "tipo_lancamento_label",
            "status_vinculo_tarifa_label",
            "confianca_vinculo_label",
            "regra_aplicada_nome",
            "regra_aplicada_texto",
            "debito",
            "credito",
            "historico_final",
            "status_aplicacao",
            "tarifa_vinculada",
            "badge_tarifa",
            "composicao",
            "is_boleto",
            "is_pix_ted",
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
            "data_ocorrencia": {"required": False, "allow_null": True},
            "lancamento_relacionado": {"required": False, "allow_null": True},
            "tipo_lancamento": {"required": False},
            "status_vinculo_tarifa": {"required": False},
            "confianca_vinculo": {"required": False},
        }

    def get_tarifa_vinculada(self, obj):
        if not obj.lancamento_relacionado:
            return None
        return {
            "id": str(obj.lancamento_relacionado.id),
            "valor": str(obj.lancamento_relacionado.valor),
            "valor_formatado": _format_currency(obj.lancamento_relacionado.valor),
            "descricao": obj.lancamento_relacionado.descricao_original[:60],
            "data_movimento": obj.lancamento_relacionado.data_movimento.isoformat() if obj.lancamento_relacionado.data_movimento else None,
            "tipo_lancamento": obj.lancamento_relacionado.tipo_lancamento,
        }

    def get_badge_tarifa(self, obj):
        if obj.tipo_lancamento != TipoLancamento.PRINCIPAL:
            return None

        desc_norm = (obj.descricao_normalizada or "").upper()
        is_pix_ted = "PIX" in desc_norm or "TED" in desc_norm

        status = obj.status_vinculo_tarifa

        if status == StatusVinculoTarifa.ENCONTRADA:
            return {"texto": "tarifa vinculada", "icone": "✔", "cor": "green"}
        if status == StatusVinculoTarifa.AGRUPADA:
            if is_pix_ted:
                return None
            return {"texto": "tarifa agrupada", "icone": "⚠", "cor": "yellow"}
        if status == StatusVinculoTarifa.NAO_ENCONTRADA:
            return None

        if is_pix_ted:
            return {"texto": "+ tarifa", "icone": "+", "cor": "gray"}
        return None

    def get_composicao(self, obj):
        componentes = obj.componentes.all()
        if not componentes:
            return None

        items = []
        total = Decimal("0")
        for comp in componentes:
            if comp.tipo_componente == TipoComponenteLancamento.PRINCIPAL:
                items.append({"tipo": "Principal", "valor": comp.valor, "valor_formatado": _format_currency(comp.valor)})
                total += comp.valor
            elif comp.tipo_componente == TipoComponenteLancamento.JUROS and comp.valor > 0:
                items.append({"tipo": "Juros", "valor": comp.valor, "valor_formatado": _format_currency(comp.valor)})
                total += comp.valor
            elif comp.tipo_componente == TipoComponenteLancamento.MULTA and comp.valor > 0:
                items.append({"tipo": "Multa", "valor": comp.valor, "valor_formatado": _format_currency(comp.valor)})
                total += comp.valor
            elif comp.tipo_componente == TipoComponenteLancamento.DESCONTO and comp.valor > 0:
                items.append({"tipo": "Desconto", "valor": comp.valor, "valor_formatado": _format_currency(comp.valor)})
                total -= comp.valor

        if len(items) <= 1:
            return None

        return {
            "itens": items,
            "total": total,
            "total_formatado": _format_currency(total),
        }

    def get_is_boleto(self, obj):
        desc_norm = (obj.descricao_normalizada or "").upper()
        return any(kw in desc_norm for kw in ["BOLETO", "PAGAMENTO", "TITULO", "COBRANCA"])

    def get_is_pix_ted(self, obj):
        if obj.tipo_lancamento == TipoLancamento.PRINCIPAL:
            desc_norm = (obj.descricao_normalizada or "").upper()
            return "PIX" in desc_norm or "TED" in desc_norm
        return False

    def get_mostrar_botao_tarifa(self, obj):
        if obj.tipo_lancamento != TipoLancamento.PRINCIPAL:
            return False
        desc_norm = (obj.descricao_normalizada or "").upper()
        is_pix_ted = "PIX" in desc_norm or "TED" in desc_norm
        if is_pix_ted:
            return False
        return obj.status_vinculo_tarifa in [
            StatusVinculoTarifa.ENCONTRADA,
            StatusVinculoTarifa.AGRUPADA,
            StatusVinculoTarifa.NAO_ENCONTRADA,
        ]

    def validate_descricao_normalizada(self, value):
        return normalize_text(value)

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

    def validate_tipo_lancamento(self, value):
        return value or TipoLancamento.PRINCIPAL

    def validate_status_vinculo_tarifa(self, value):
        return value or StatusVinculoTarifa.NAO_APLICA

    def validate_confianca_vinculo(self, value):
        return value or ConfiancaVinculo.BAIXA

    def validate_dados_brutos(self, value):
        return value if isinstance(value, dict) else {}

    def _sync_componentes(self, instance, componentes_data):
        existing = {str(obj.id): obj for obj in instance.componentes.all()}
        keep_ids = set()

        for item in componentes_data:
            component_id = str(item.get("id") or "").strip()
            payload = {
                "tipo_componente": item.get("tipo_componente") or TipoComponenteLancamento.PRINCIPAL,
                "valor": item.get("valor") or Decimal("0"),
                "descricao": _strip_text(item.get("descricao")),
            }

            if component_id and component_id in existing:
                component = existing[component_id]
                changed_fields = []
                for field_name, value in payload.items():
                    if getattr(component, field_name) != value:
                        setattr(component, field_name, value)
                        changed_fields.append(field_name)
                if changed_fields:
                    component.save(update_fields=[*changed_fields, "atualizado_em"])
                keep_ids.add(component_id)
                continue

            component = LancamentoComponente.objects.create(lancamento=instance, **payload)
            keep_ids.add(str(component.id))

        instance.componentes.exclude(id__in=keep_ids).delete()

    def create(self, validated_data):
        componentes_data = validated_data.pop("componentes", [])
        instance = super().create(validated_data)
        if componentes_data:
            self._sync_componentes(instance, componentes_data)
        return instance

    def update(self, instance, validated_data):
        componentes_data = validated_data.pop("componentes", None)
        instance = super().update(instance, validated_data)
        if componentes_data is not None:
            self._sync_componentes(instance, componentes_data)
        return instance

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


class PlanoContasSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanoContas
        fields = ["id", "codigo", "classificacao", "nome", "tipo", "natureza", "ativo"]
        extra_kwargs = {
            "classificacao": {"required": False, "allow_blank": True},
            "tipo": {"required": False, "allow_blank": True},
            "natureza": {"required": False, "allow_blank": True},
        }


class HistoricoContabilSerializer(serializers.ModelSerializer):
    class Meta:
        model = HistoricoContabil
        fields = ["id", "codigo", "nome", "grupo", "ativo"]
        extra_kwargs = {
            "grupo": {"required": False, "allow_blank": True},
        }


class BancoSerializer(serializers.ModelSerializer):
    logo_url = serializers.SerializerMethodField()
    remover_logo = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = Banco
        fields = [
            "id",
            "codigo",
            "nome",
            "slug",
            "sigla",
            "cor_principal",
            "logo",
            "logo_url",
            "ativo",
            "remover_logo",
            "criado_em",
            "atualizado_em",
        ]
        read_only_fields = ["id", "logo_url", "criado_em", "atualizado_em"]
        extra_kwargs = {
            "sigla": {"required": False, "allow_blank": True},
            "cor_principal": {"required": False, "allow_blank": True},
            "logo": {"required": False, "allow_empty_file": False},
        }

    def validate_codigo(self, value):
        return _strip_text(value)

    def validate_nome(self, value):
        return _strip_text(value)

    def validate_slug(self, value):
        return _strip_text(value).lower()

    def validate_sigla(self, value):
        return _strip_text(value).upper()

    def validate_cor_principal(self, value):
        value = _strip_text(value) or "#64748b"
        if not value.startswith("#"):
            value = f"#{value}"
        return value

    def create(self, validated_data):
        validated_data.pop("remover_logo", False)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        remover_logo = validated_data.pop("remover_logo", False)
        if remover_logo and instance.logo:
            instance.logo.delete(save=True)
        return super().update(instance, validated_data)

    def get_logo_url(self, obj):
        if not obj.logo:
            return ""
        request = self.context.get("request")
        if request is not None:
            return request.build_absolute_uri(obj.logo.url)
        return obj.logo.url
