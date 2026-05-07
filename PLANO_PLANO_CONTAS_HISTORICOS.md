# Plano de Implementação — Plano de Contas, Históricos e Correção de Contas Bancárias

> **Status das perguntas respondidas** — tudo confirmado, pronto para implementar.

## O que será feito

### Decisões confirmadas
| Pergunta | Resposta |
|---|---|
| Plano de contas é global ou por cliente? | **Global do escritório** — todos os clientes usam o mesmo plano |
| Salvar código ou código+nome? | **Só o código numérico** (ex: `237`) |
| Campo `codigo_contabil` na conta bancária | Já existe no banco (migration 0011) — só corrigir a tela |
| Mostrar contas sintéticas? | **Sim, mostrar todas** — o usuário precisa poder editar inclusive as sintéticas |
| Plano de contas é editável? | **Sim, CRUD completo** — os códigos foram definidos pela empresa e podem mudar |

---

## Parte 0 — CORREÇÃO URGENTE: Formulário de conta bancária

### Problema atual
O formulário "Bancos e contas" (aba Dados do cliente) tem um select de **Tipo** com duas opções: "Conta bancária" e "Conta contábil". O usuário quer que seja **uma única forma**: conta bancária com campo de código contábil junto.

### O que está errado no código
Em `ContaCliente.save()`, quando `tipo=BANCARIA`, os campos `codigo_contabil` e `descricao_contabil` são **apagados automaticamente**. Isso impede salvar o código contábil em uma conta bancária.

### Correção
1. **`app/models.py`** — remover a limpeza de `codigo_contabil`/`descricao_contabil` quando `tipo=BANCARIA`
2. **`section_cliente.html`** — remover o select de Tipo do formulário (sempre será BANCARIA), mover o campo `codigo_contabil` para dentro da seção bancária, remover a seção "contábil" separada
3. **`app.js`** — remover a lógica que mostra/esconde campos baseada no tipo selecionado

> Contas do tipo CONTABIL que já existem no banco não serão apagadas — apenas não aparecerão mais para criação nova.

---

## Parte 1 — Models e Migrations: `PlanoContas` e `HistoricoContabil`

Criar dois novos models. São **globais por escritório** (todos os clientes compartilham o mesmo plano).

### `PlanoContas`
| Campo | Tipo | Descrição |
|---|---|---|
| `id` | UUID PK | — |
| `escritorio` | FK → Escritorio | dono do plano |
| `codigo` | CharField(20) | ex: `237`, `98`, `602` — editável pelo usuário |
| `classificacao` | CharField(50) | ex: `1.10.10.20.02` |
| `nome` | CharField(255) | ex: `Banco Bradesco` |
| `tipo` | CharField(30) | `Sintética`, `Analitica`, `Banco`, `Caixa` |
| `natureza` | CharField(50) | ex: `Contas do Ativo` |
| `ativo` | BooleanField | default True |

### `HistoricoContabil`
| Campo | Tipo | Descrição |
|---|---|---|
| `id` | UUID PK | — |
| `escritorio` | FK → Escritorio | — |
| `codigo` | IntegerField | ex: `1`, `42`, `180` |
| `nome` | CharField(255) | ex: `Nota Fiscal nº`, `Receita de Serviços` |
| `grupo` | CharField(120) | ex: `Escrituração Fiscal`, `Receitas` |
| `ativo` | BooleanField | default True |

---

## Parte 2 — API: endpoints de leitura + CRUD

### Leitura (para popular os selects no frontend)
```
GET /api/plano-contas/         → lista todos do escritório
GET /api/historico-contabil/   → lista todos do escritório
```

### CRUD (para o usuário editar o plano)
```
POST   /api/plano-contas/          → criar nova conta
PATCH  /api/plano-contas/<id>/     → editar
DELETE /api/plano-contas/<id>/     → excluir
POST   /api/historico-contabil/    → criar
PATCH  /api/historico-contabil/<id>/
DELETE /api/historico-contabil/<id>/
```

> Na primeira iteração, somente o GET será usado pelo conciliador. O CRUD será exposto na API mas a tela de gerenciamento pode ser adicionada depois se necessário.

---

## Parte 3 — Seed: importar dados das planilhas via migration

Migration de dados embutindo os registros das planilhas já fornecidas:

- **1000 contas** do Plano de Contas (Código, Classificação, Nome, Tipo, Natureza)
- **325 históricos** (Id, Nome, Grupo)

Os dados foram convertidos e verificados em `/tmp/Plano de Contas.csv` e `/tmp/Históricos.csv`.

---

## Parte 4 — Frontend: combobox com busca

Nos campos `conta_débito`, `conta_crédito` e `código_histórico`, substituir os `<input type="text">` por um **combobox de busca** (JS puro, sem biblioteca).

### Comportamento
- Clicou → abre dropdown com lista completa
- Digitou → filtra por código **ou** nome (ex: `237` ou `brad` → mostra Bradesco)
- Selecionou → fecha, salva **só o código** no campo hidden, exibe `237 — Banco Bradesco` no campo visual
- Esc ou clique fora → fecha sem alterar
- Campo vazio (limpar) → permite limpar a seleção

### Locais de aplicação
| Local | Campos |
|---|---|
| Form de regra (aba Regras do cliente) | `conta_debito`, `conta_credito`, `codigo_historico` |
| Modal de lançamento individual (detalhe transação) | `conta_debito`, `conta_credito`, `codigo_historico` |

### Pré-preenchimento automático
Quando abrir o form de regra/lançamento, o sistema lê `codigo_contabil` da conta bancária da empresa selecionada e pré-preenche automaticamente o campo correto:
- Movimento **Crédito** → pré-preenche `conta_credito` com o código contábil do banco
- Movimento **Débito** → pré-preenche `conta_debito` com o código contábil do banco
- O outro campo fica em aberto para o usuário preencher com a contrapartida

---

## Ordem de execução

| # | O quê | Arquivos |
|---|---|---|
| 0 | Corrigir form de conta bancária (remover tipo, adicionar codigo_contabil) | `models.py`, `section_cliente.html`, `app.js` |
| 1 | Models `PlanoContas` e `HistoricoContabil` | `app/models.py` |
| 2 | Migration + seed de dados | `app/migrations/0013_plano_contas_historico.py` + `0014_seed_*.py` |
| 3 | Serializers + API views + urls | `serializers.py`, `api_views.py`, `api_urls.py` |
| 4 | Componente combobox JS | `app.js` |
| 5 | Combobox nas regras (aba Regras do cliente) | `app.js`, `section_conciliador.html` |
| 6 | Combobox no modal de lançamento | `app.js` |
| 7 | Pré-preenchimento automático da conta do banco | `app.js` |
