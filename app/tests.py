from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase

from app.models import TipoComparacao
from services.conciliador import _rule_matches_description, normalizar_descricao_transacao


class TransactionDescriptionNormalizationTests(SimpleTestCase):
    def test_normaliza_descricoes_bancarias_equivalentes(self):
        cases = {
            "Pix-Recebido QR Code": "PIX RECEBIDO QR CODE",
            "Pix - Recebido QR Code — ANTONIA LUA": "PIX RECEBIDO QR CODE",
            "TED-Recebida 4981164450": "TED RECEBIDA",
            "Pagamento - Boleto - JOAO DA SILVA": "PAGAMENTO BOLETO",
            "Transferência Recebida — MARIA SOUZA": "TRANSFERENCIA RECEBIDA",
            "Débito Automático": "DEBITO AUTOMATICO",
            "Compra-Cartão": "COMPRA CARTAO",
            "Tarifa Bancária": "TARIFA BANCARIA",
            "13113 258 Tarifa Pix Enviado — Tar. agrupadas - ocorrencia 05/01/2024": "TARIFA PIX ENVIADO",
        }

        for original, expected in cases.items():
            with self.subTest(original=original):
                self.assertEqual(normalizar_descricao_transacao(original), expected)

    def test_regra_compara_pela_chave_normalizada(self):
        rule = SimpleNamespace(
            texto_referencia="Pix-Recebido QR Code",
            tipo_comparacao=TipoComparacao.IGUAL,
        )

        self.assertTrue(_rule_matches_description(rule, "Pix - Recebido QR Code — ANTONIA LUA"))

    def test_remove_codigos_soltos_no_final_sem_perder_tipo(self):
        self.assertEqual(
            normalizar_descricao_transacao("PIX RECEBIDO QR CODE 4981164450"),
            "PIX RECEBIDO QR CODE",
        )
