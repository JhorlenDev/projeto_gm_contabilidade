from __future__ import annotations

import uuid
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

from services.secure_storage import encrypted_private_storage


def _generate_code(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def _conciliador_upload_to(instance, filename: str) -> str:
    extension = Path(filename or "arquivo").suffix.lower() or ".dat"
    escritorio = getattr(instance, "escritorio_id", None) or "sem-escritorio"
    referencia = getattr(instance, "referencia", None) or "sem-referencia"
    return f"conciliador/{escritorio}/{referencia}/{uuid.uuid4().hex}{extension}"


class TipoArquivo(models.TextChoices):
    CSV = "CSV", "CSV"
    XLS = "XLS", "XLS"
    XLSX = "XLSX", "XLSX"
    PDF = "PDF", "PDF"


class StatusImportacao(models.TextChoices):
    ENVIADA = "ENVIADA", "Enviada"
    PROCESSANDO = "PROCESSANDO", "Processando"
    PROCESSADA = "PROCESSADA", "Processada"
    ERRO = "ERRO", "Erro"


class TipoMovimento(models.TextChoices):
    CREDITO = "CREDITO", "Crédito"
    DEBITO = "DEBITO", "Débito"
    AMBOS = "AMBOS", "Ambos"


class TipoContaCliente(models.TextChoices):
    BANCARIA = "BANCARIA", "Conta bancária"
    CONTABIL = "CONTABIL", "Conta contábil"


class TipoComparacao(models.TextChoices):
    CONTEM = "CONTEM", "Contém"
    IGUAL = "IGUAL", "Igual"
    COMECA_COM = "COMECA_COM", "Começa com"


class Cliente(models.Model):
    class Situacao(models.TextChoices):
        ATIVO = "ATIVO", "Ativo"
        EM_ANALISE = "EM_ANALISE", "Em análise"
        PAUSADO = "PAUSADO", "Pausado"
        INATIVO = "INATIVO", "Inativo"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    codigo = models.CharField(max_length=20, unique=True, blank=True, default="", db_index=True)
    nome = models.CharField(max_length=255)
    cpf_cnpj = models.CharField(max_length=20, db_index=True)
    email = models.EmailField(blank=True, default="")
    ie = models.CharField(max_length=32, blank=True, default="")
    telefone = models.CharField(max_length=20, blank=True, default="")
    conta_corrente = models.CharField(max_length=30, blank=True, default="", verbose_name="Conta corrente")
    conta_contabil = models.CharField(max_length=20, blank=True, default="", verbose_name="Conta contábil (cód. banco)")
    data_inicio = models.DateField(default=timezone.localdate)
    situacao = models.CharField(max_length=20, choices=Situacao.choices, default=Situacao.ATIVO)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nome"]
        indexes = [
            models.Index(fields=["codigo"]),
            models.Index(fields=["nome"]),
            models.Index(fields=["cpf_cnpj"]),
            models.Index(fields=["situacao"]),
        ]

    def save(self, *args, **kwargs):
        if not self.codigo:
            self.codigo = _generate_code("CLI")
        else:
            self.codigo = str(self.codigo).strip().upper()

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.codigo} - {self.nome}"


class ContaCliente(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="contas")
    tipo = models.CharField(max_length=20, choices=TipoContaCliente.choices, db_index=True)
    apelido = models.CharField(max_length=120, blank=True, default="")
    banco = models.CharField(max_length=120, blank=True, default="")
    agencia = models.CharField(max_length=20, blank=True, default="")
    numero = models.CharField(max_length=30, blank=True, default="")
    codigo_contabil = models.CharField(max_length=40, blank=True, default="")
    descricao_contabil = models.CharField(max_length=255, blank=True, default="")
    observacoes = models.TextField(blank=True, default="")
    ativo = models.BooleanField(default=True, db_index=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-ativo", "tipo", "apelido", "criado_em"]
        indexes = [
            models.Index(fields=["cliente", "ativo"]),
            models.Index(fields=["cliente", "tipo"]),
        ]

    def save(self, *args, **kwargs):
        self.apelido = str(self.apelido or "").strip()
        self.banco = str(self.banco or "").strip()
        self.agencia = str(self.agencia or "").strip()
        self.numero = str(self.numero or "").strip()
        self.codigo_contabil = str(self.codigo_contabil or "").strip()
        self.descricao_contabil = str(self.descricao_contabil or "").strip()
        self.observacoes = str(self.observacoes or "").strip()

        if self.tipo == TipoContaCliente.BANCARIA:
            self.codigo_contabil = ""
            self.descricao_contabil = ""
        elif self.tipo == TipoContaCliente.CONTABIL:
            self.banco = ""
            self.agencia = ""
            self.numero = ""

        super().save(*args, **kwargs)

    def clean(self):
        errors = {}

        if self.tipo == TipoContaCliente.BANCARIA:
            if not self.banco:
                errors["banco"] = "Informe o banco."
            if not self.agencia:
                errors["agencia"] = "Informe a agência."
            if not self.numero:
                errors["numero"] = "Informe o número da conta."
        elif self.tipo == TipoContaCliente.CONTABIL:
            if not self.codigo_contabil:
                errors["codigo_contabil"] = "Informe o código contábil."
        else:
            errors["tipo"] = "Tipo de conta inválido."

        if errors:
            raise ValidationError(errors)

    def resumo(self) -> str:
        if self.tipo == TipoContaCliente.BANCARIA:
            partes = [self.banco, f"Ag. {self.agencia}" if self.agencia else "", f"Conta {self.numero}" if self.numero else ""]
            partes = [parte for parte in partes if parte]
            return " · ".join(partes) or self.apelido or "Conta bancária"

        partes = [self.codigo_contabil, self.descricao_contabil]
        partes = [parte for parte in partes if parte]
        return " · ".join(partes) or self.apelido or "Conta contábil"

    def __str__(self):
        return f"{self.cliente} - {self.resumo()}"


def _certificado_upload_to(instance, filename: str) -> str:
    extension = Path(filename or "certificado").suffix.lower() or ".pfx"
    cliente = getattr(instance, "cliente_id", None) or "sem-cliente"
    return f"certificados/{cliente}/{uuid.uuid4().hex}{extension}.enc"


class CertificadoDigitalCliente(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cliente = models.OneToOneField(Cliente, on_delete=models.PROTECT, related_name="certificado_digital")
    arquivo = models.FileField(upload_to=_certificado_upload_to, storage=encrypted_private_storage, max_length=255)
    arquivo_original = models.CharField(max_length=255)
    tipo_arquivo = models.CharField(max_length=10, choices=[("PFX", "PFX"), ("P12", "P12")], db_index=True)
    tamanho_bytes = models.PositiveBigIntegerField(default=0)
    hash_sha256 = models.CharField(max_length=64, blank=True, default="", db_index=True)
    ativo = models.BooleanField(default=True, db_index=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-ativo", "cliente__nome"]

    def save(self, *args, **kwargs):
        self.arquivo_original = str(self.arquivo_original or "").strip()
        self.tipo_arquivo = str(self.tipo_arquivo or "PFX").upper().strip() or "PFX"
        self.hash_sha256 = str(self.hash_sha256 or "").strip().lower()
        super().save(*args, **kwargs)

    def clean(self):
        errors = {}
        original = str(self.arquivo_original or getattr(self.arquivo, "name", "")).strip()
        suffix = Path(original).suffix.lower()

        if not self.cliente_id:
            errors["cliente"] = "Informe o cliente."

        if not original:
            errors["arquivo"] = "Envie um certificado digital."
        elif suffix not in {".pfx", ".p12"}:
            errors["arquivo"] = "Use arquivos .pfx ou .p12."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.cliente} - certificado digital"


@receiver(post_delete, sender=CertificadoDigitalCliente)
def _delete_certificado_digital_file(_sender, instance, **_kwargs):
    arquivo = getattr(instance, "arquivo", None)
    arquivo_name = getattr(arquivo, "name", "")
    if arquivo_name and getattr(arquivo, "storage", None):
        arquivo.storage.delete(arquivo_name)


class Escritorio(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nome = models.CharField(max_length=255, unique=True)
    cnpj = models.CharField(max_length=20, blank=True, default="", db_index=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class ImportacaoExtrato(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    escritorio = models.ForeignKey(Escritorio, on_delete=models.PROTECT, related_name="importacoes_extrato")
    empresa = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="importacoes_extrato")
    referencia = models.CharField(max_length=7, db_index=True)
    conta_bancaria = models.CharField(max_length=120, blank=True, default="")
    arquivo = models.FileField(upload_to=_conciliador_upload_to, max_length=255)
    tipo_arquivo = models.CharField(max_length=10, choices=TipoArquivo.choices, db_index=True)
    status = models.CharField(max_length=20, choices=StatusImportacao.choices, default=StatusImportacao.ENVIADA)
    configuracao = models.JSONField(default=dict, blank=True)
    metadados = models.JSONField(default=dict, blank=True)
    mensagem_erro = models.TextField(blank=True, default="")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["referencia"]),
            models.Index(fields=["tipo_arquivo"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.escritorio} - {self.empresa} - {self.referencia}"


class RegraConciliador(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    escritorio = models.ForeignKey(Escritorio, on_delete=models.PROTECT, related_name="regras_conciliador")
    empresa = models.ForeignKey(
        Cliente,
        on_delete=models.PROTECT,
        related_name="regras_conciliador",
        null=True,
        blank=True,
    )
    nome = models.CharField(max_length=255)
    texto_referencia = models.CharField(max_length=255)
    tipo_comparacao = models.CharField(max_length=20, choices=TipoComparacao.choices, default=TipoComparacao.CONTEM)
    tipo_movimento = models.CharField(max_length=20, choices=TipoMovimento.choices, default=TipoMovimento.AMBOS)
    categoria = models.CharField(max_length=120, blank=True, default="")
    subcategoria = models.CharField(max_length=120, blank=True, default="")
    codigo_historico = models.CharField(max_length=120, blank=True, default="")
    conta_debito = models.CharField(max_length=120, blank=True, default="")
    conta_credito = models.CharField(max_length=120, blank=True, default="")
    aplicar_automatico = models.BooleanField(default=True)
    prioridade = models.PositiveIntegerField(default=100, db_index=True)
    ativo = models.BooleanField(default=True, db_index=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["prioridade", "nome"]
        indexes = [
            models.Index(fields=["escritorio", "ativo", "prioridade"]),
            models.Index(fields=["empresa", "ativo"]),
        ]

    def __str__(self):
        return self.nome


class TransacaoImportada(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    importacao = models.ForeignKey(ImportacaoExtrato, on_delete=models.CASCADE, related_name="transacoes")
    linha_origem = models.PositiveIntegerField(null=True, blank=True)
    data_movimento = models.DateField(db_index=True)
    descricao_original = models.TextField()
    descricao_normalizada = models.TextField(blank=True, default="", db_index=True)
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    tipo_movimento = models.CharField(max_length=20, choices=TipoMovimento.choices, db_index=True)
    regra_aplicada = models.ForeignKey(
        RegraConciliador,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transacoes",
    )
    categoria = models.CharField(max_length=120, blank=True, default="")
    subcategoria = models.CharField(max_length=120, blank=True, default="")
    conta_debito = models.CharField(max_length=120, blank=True, default="")
    conta_credito = models.CharField(max_length=120, blank=True, default="")
    codigo_historico = models.CharField(max_length=120, blank=True, default="")
    revisado_manual = models.BooleanField(default=False, db_index=True)
    dados_brutos = models.JSONField(default=dict, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["data_movimento", "id"]
        indexes = [
            models.Index(fields=["importacao", "data_movimento"]),
            models.Index(fields=["importacao", "tipo_movimento"]),
            models.Index(fields=["regra_aplicada"]),
        ]

    def __str__(self):
        return f"{self.data_movimento} - {self.descricao_original[:40]}"


class ExtratoHistorico(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    escritorio = models.ForeignKey(Escritorio, on_delete=models.PROTECT, related_name="extrato_historicos")
    empresa = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="extrato_historicos")
    banco = models.CharField(max_length=60, blank=True, default="")
    periodo_inicio = models.DateField(null=True, blank=True)
    periodo_fim = models.DateField(null=True, blank=True)
    total_lancamentos = models.PositiveIntegerField(default=0)
    # JSON com estado completo: lancamentos, regras, componentes, comprovantes
    dados = models.JSONField(default=dict)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criado_em"]
        indexes = [
            models.Index(fields=["empresa", "criado_em"]),
            models.Index(fields=["escritorio", "criado_em"]),
        ]

    def __str__(self):
        return f"{self.empresa} | {self.banco} | {self.periodo_inicio}"


class PerfilConciliacao(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    escritorio = models.ForeignKey(Escritorio, on_delete=models.PROTECT, related_name="perfis_conciliacao")
    empresa = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="perfis_conciliacao")
    nome = models.CharField(max_length=255)
    descricao = models.TextField(blank=True, default="")
    conta_bancaria = models.CharField(max_length=120, blank=True, default="")
    codigo_historico = models.CharField(max_length=120, blank=True, default="")
    codigo_empresa = models.CharField(max_length=120, blank=True, default="")
    cnpj = models.CharField(max_length=20, blank=True, default="")
    parametros = models.JSONField(default=list, blank=True)
    ativo = models.BooleanField(default=True, db_index=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nome"]
        indexes = [
            models.Index(fields=["escritorio", "ativo"]),
            models.Index(fields=["empresa", "ativo"]),
        ]

    def __str__(self):
        return self.nome


class KeycloakUser(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sub = models.CharField(max_length=255, unique=True, db_index=True)
    nome = models.CharField(max_length=255, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    roles = models.JSONField(default=list, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nome", "email"]

    def __str__(self):
        return self.nome or self.email or self.sub
