# Plano de Migração: Django Templates → Next.js

> **Status:** Planejamento  
> **Backend:** Django 5.2 (API pura, sem alteração nos models/migrations)  
> **Frontend novo:** Next.js 15 + TypeScript + Tailwind CSS  
> **Auth:** Keycloak (mesmo servidor, mesmo realm)

---

## Arquitetura final

```
┌─────────────────────────────────────────────────────────────┐
│                        Usuário (browser)                    │
└────────────────────────┬────────────────────────────────────┘
                         │
              ┌──────────▼──────────┐
              │   Next.js (port 3000)│  ← novo frontend
              │   App Router + TS   │
              └──────────┬──────────┘
                         │  fetch /api/* com Bearer token
              ┌──────────▼──────────┐
              │  Django (port 8000) │  ← backend puro, sem HTML
              │  REST API + DRF     │
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │  PostgreSQL (8002)  │
              └─────────────────────┘

              ┌─────────────────────┐
              │  Keycloak (externo) │  ← sem alteração
              │  realm gm           │
              └─────────────────────┘
```

---

## Fase 1 — Preparar o backend Django (1-2h)

### 1.1 CORS — liberar o Next.js

No `.env` do Django, adicionar as origens do Next.js:

```env
# desenvolvimento
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000

# produção (adicionar quando tiver o domínio)
# CORS_ALLOWED_ORIGINS=https://app.gmcontabilidade.com.br,http://localhost:8000
```

O `settings.py` já lê essa variável via `decouple` — nenhuma alteração no código.

### 1.2 Adicionar CORS para credenciais (cookies do Next.js)

Em `config/settings.py`, adicionar após `CORS_ALLOWED_ORIGINS`:

```python
CORS_ALLOW_CREDENTIALS = True

CORS_ALLOW_HEADERS = [
    "accept",
    "authorization",
    "content-type",
    "origin",
    "x-csrftoken",
    "x-requested-with",
]
```

### 1.3 Remover views de página (opcional — fazer no final)

Só após o Next.js estar funcionando 100%:
- Remover `login_page` e `panel_page` de `app/views.py`
- Remover `app/urls.py` 
- Remover `path("", include("app.urls"))` de `config/urls.py`
- Remover a pasta `app/templates/` e `app/static/`

> **Atenção:** Não fazer isso antes — manter o frontend antigo funcionando durante a migração.

### 1.4 APIs que já existem e funcionam

Todas essas já estão prontas e documentadas implicitamente:

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `POST /api/auth/keycloak/token/` | POST | Troca code → tokens |
| `GET/POST /api/clientes/` | GET POST | Lista e cria clientes |
| `GET/PATCH/DELETE /api/clientes/{id}/` | PATCH DELETE | Detalhe, edição, remoção |
| `GET/POST /api/contas-clientes/` | GET POST | Contas bancárias |
| `GET/POST /api/conciliador-importacoes/` | GET POST | Importações de extrato |
| `GET/POST /api/conciliador-transacoes/` | GET POST | Transações importadas |
| `GET/POST /api/conciliador-regras/` | GET POST | Regras de conciliação |
| `GET/POST /api/conciliador-perfis/` | GET POST | Perfis de importação |
| `GET/POST /api/plano-contas/` | GET POST | Plano de contas |
| `PATCH/DELETE /api/plano-contas/{id}/` | PATCH DELETE | Editar/inativar conta |
| `GET/POST /api/historico-contabil/` | GET POST | Históricos contábeis |
| `PATCH/DELETE /api/historico-contabil/{id}/` | PATCH DELETE | Editar/inativar histórico |
| `POST /api/extrato-preview/` | POST | Parse do PDF do extrato |
| `POST /api/comprovante-preview/` | POST | Parse do PDF do comprovante |
| `GET/DELETE /api/extrato-historico/` | GET DELETE | Histórico de extratos |
| `GET /api/escritorios/` | GET | Dados do escritório |

---

## Fase 2 — Criar o projeto Next.js (1h)

### 2.1 Criar o projeto

```bash
cd /home/vini/Documentos/gm_git

npx create-next-app@latest gm-frontend \
  --typescript \
  --tailwind \
  --eslint \
  --app \
  --src-dir \
  --import-alias "@/*" \
  --no-turbopack
```

### 2.2 Instalar dependências

```bash
cd gm-frontend

# autenticação Keycloak
npm install next-auth@beta

# fetch + cache de dados
npm install @tanstack/react-query @tanstack/react-query-devtools

# formulários + validação
npm install react-hook-form zod @hookform/resolvers

# componentes UI acessíveis (Radix)
npm install @radix-ui/react-dialog
npm install @radix-ui/react-tabs
npm install @radix-ui/react-popover
npm install @radix-ui/react-select
npm install @radix-ui/react-checkbox
npm install @radix-ui/react-label
npm install @radix-ui/react-tooltip

# ícones
npm install lucide-react

# utilitários de className
npm install clsx tailwind-merge

# upload de arquivos
npm install react-dropzone
```

### 2.3 Estrutura de pastas

```
gm-frontend/
├── src/
│   ├── app/
│   │   ├── layout.tsx                    ← providers globais (QueryClient, Session)
│   │   ├── page.tsx                      ← redirect → /painel
│   │   ├── globals.css                   ← cores do sistema GM
│   │   ├── login/
│   │   │   └── page.tsx                  ← página de login
│   │   ├── api/
│   │   │   └── auth/
│   │   │       └── [...nextauth]/
│   │   │           └── route.ts          ← handler do NextAuth
│   │   └── painel/
│   │       ├── layout.tsx                ← sidebar + topbar + proteção de rota
│   │       ├── page.tsx                  ← dashboard
│   │       ├── clientes/
│   │       │   └── page.tsx
│   │       ├── conciliador/
│   │       │   └── page.tsx
│   │       ├── perfis/
│   │       │   └── page.tsx
│   │       └── contabilidade/
│   │           └── page.tsx
│   │
│   ├── components/
│   │   ├── ui/                           ← componentes base reutilizáveis
│   │   │   ├── Button.tsx
│   │   │   ├── Input.tsx
│   │   │   ├── Badge.tsx
│   │   │   ├── Dialog.tsx
│   │   │   ├── Table.tsx
│   │   │   ├── Tabs.tsx
│   │   │   ├── Combobox.tsx              ← substitui setupCombobox()
│   │   │   └── Spinner.tsx
│   │   ├── layout/
│   │   │   ├── Sidebar.tsx
│   │   │   ├── Topbar.tsx
│   │   │   └── PainelLayout.tsx
│   │   ├── clientes/
│   │   │   ├── ClienteList.tsx
│   │   │   ├── ClienteCard.tsx
│   │   │   ├── ClienteForm.tsx
│   │   │   └── ContaClienteSection.tsx
│   │   ├── conciliador/
│   │   │   ├── ExtratoUploadForm.tsx
│   │   │   ├── ExtratoTable.tsx
│   │   │   ├── DetalheModal.tsx          ← modal central 2 colunas
│   │   │   ├── RegrasForm.tsx
│   │   │   └── HistoricoExtratos.tsx
│   │   ├── perfis/
│   │   │   ├── PerfisList.tsx
│   │   │   └── PerfilForm.tsx
│   │   └── contabilidade/
│   │       ├── PlanoContasTable.tsx
│   │       ├── HistoricoContabilTable.tsx
│   │       └── ContabForm.tsx
│   │
│   ├── lib/
│   │   ├── api.ts                        ← fetch wrapper com auth automático
│   │   ├── auth.ts                       ← config NextAuth + Keycloak
│   │   └── utils.ts                      ← cn(), formatBRL(), etc.
│   │
│   ├── hooks/
│   │   ├── useClientes.ts
│   │   ├── usePlanoContas.ts
│   │   ├── useHistoricos.ts
│   │   ├── useConciliador.ts
│   │   └── useEscritorio.ts
│   │
│   └── types/
│       ├── cliente.ts
│       ├── conciliador.ts
│       ├── contabilidade.ts
│       └── auth.ts
```

---

## Fase 3 — Autenticação Keycloak com NextAuth (2-3h)

### 3.1 Arquivo `src/lib/auth.ts`

```typescript
import NextAuth from "next-auth"
import Keycloak from "next-auth/providers/keycloak"

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [
    Keycloak({
      clientId: process.env.KEYCLOAK_CLIENT_ID!,
      clientSecret: process.env.KEYCLOAK_CLIENT_SECRET!,
      issuer: process.env.KEYCLOAK_ISSUER!,
    }),
  ],
  callbacks: {
    async jwt({ token, account }) {
      // salva o access_token do Keycloak no JWT interno do Next.js
      if (account) {
        token.accessToken = account.access_token
        token.refreshToken = account.refresh_token
        token.expiresAt = account.expires_at
      }
      return token
    },
    async session({ session, token }) {
      // expõe o accessToken para o frontend
      session.accessToken = token.accessToken as string
      return session
    },
  },
})
```

### 3.2 Arquivo `src/app/api/auth/[...nextauth]/route.ts`

```typescript
import { handlers } from "@/lib/auth"
export const { GET, POST } = handlers
```

### 3.3 Middleware de proteção `src/middleware.ts`

```typescript
import { auth } from "@/lib/auth"
import { NextResponse } from "next/server"

export default auth((req) => {
  const isLoggedIn = !!req.auth
  const isOnPainel = req.nextUrl.pathname.startsWith("/painel")

  if (isOnPainel && !isLoggedIn) {
    return NextResponse.redirect(new URL("/login", req.nextUrl))
  }
})

export const config = {
  matcher: ["/painel/:path*"],
}
```

### 3.4 Variáveis de ambiente `.env.local`

```env
NEXTAUTH_URL=http://localhost:3000
NEXTAUTH_SECRET=gere-com-openssl-rand-base64-32

KEYCLOAK_CLIENT_ID=gm-frontend
KEYCLOAK_CLIENT_SECRET=pegar-no-keycloak-admin
KEYCLOAK_ISSUER=https://seu-keycloak.com/realms/gm

NEXT_PUBLIC_API_URL=http://localhost:8000
```

> **Nota:** Criar um novo Client no Keycloak Admin chamado `gm-frontend` com tipo `confidential`, redirect URI `http://localhost:3000/api/auth/callback/keycloak`.

### 3.5 Fetch wrapper `src/lib/api.ts`

```typescript
import { auth } from "@/lib/auth"

const BASE = process.env.NEXT_PUBLIC_API_URL

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const session = await auth()

  const res = await fetch(`${BASE}/api${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session?.accessToken}`,
      ...options.headers,
    },
  })

  if (!res.ok) {
    const error = await res.json().catch(() => ({}))
    throw new Error(JSON.stringify(error) || `Erro ${res.status}`)
  }

  return res.json()
}
```

---

## Fase 4 — Layout do painel (3-4h)

### 4.1 `src/app/painel/layout.tsx`

Server Component que carrega sessão e renderiza:
- `<Sidebar>` com navegação por links (`<Link href="/painel/clientes">`)
- `<Topbar>` com nome do usuário e botão sair
- `{children}` — a página atual

### 4.2 Sidebar

Substituir o sistema de abas JS (`data-panel-view-target`) por roteamento real do Next.js. Cada item do menu é um `<Link>`. O item ativo é detectado com `usePathname()`.

```typescript
// componentes/layout/Sidebar.tsx
"use client"
import Link from "next/link"
import { usePathname } from "next/navigation"

const items = [
  { href: "/painel",              label: "Dashboard"      },
  { href: "/painel/conciliador",  label: "Conciliador"    },
  { href: "/painel/clientes",     label: "Clientes"       },
  { href: "/painel/perfis",       label: "Perfis"         },
  { href: "/painel/contabilidade",label: "Contabilidade"  },
]
```

---

## Fase 5 — Migração das seções (ordem recomendada)

### 5.1 Dashboard (2h)

- Buscar total de clientes: `GET /api/clientes/?page_size=1` pega o `count`
- Cards de estatísticas como Server Components (sem estado)

### 5.2 Contabilidade (4h)

A mais simples — é só CRUD de tabela.

- `usePlanoContas()` → `useQuery` em `/api/plano-contas/?todos=1`
- `useHistoricos()` → `useQuery` em `/api/historico-contabil/?todos=1`
- Componente `<Combobox>` reutilizável com Radix `Popover` + input de pesquisa
- `useMutation` para POST/PATCH/DELETE com `invalidateQueries` após sucesso

### 5.3 Clientes (6h)

- Lista paginada com busca e filtro de status
- Dialog de criação/edição com `react-hook-form` + `zod`
- Seção de contas bancárias dentro do cliente
- Seção de certificados digitais

### 5.4 Perfis de Conciliação (4h)

- Lista de perfis
- Form de configuração (banco, empresa, formato)
- Duplicar perfil

### 5.5 Conciliador (10h — mais complexo)

1. **Upload do extrato** — `react-dropzone`, envia para `POST /api/extrato-preview/`
2. **Tabela de transações** — virtualização com `@tanstack/react-virtual` se tiver muitas linhas
3. **Modal de detalhe** — `<Dialog>` Radix, layout 2 colunas com CSS Grid
4. **Combobox plano/histórico** — componente `<Combobox>` alimentado pelos hooks
5. **Regras de conciliação** — fetch/save por transação
6. **Exportação XLS** — `xlsx` lib igual ao atual, mas como download direto

---

## Fase 6 — Docker e deploy (2h)

### 6.1 Adicionar serviço Next.js no `docker-compose.yml`

```yaml
services:
  db:        # igual ao atual
    ...

  django:
    build: .
    ports:
      - "8000:8000"
    env_file: .env
    depends_on:
      db:
        condition: service_healthy

  nextjs:
    build:
      context: ../gm-frontend
      dockerfile: Dockerfile
    ports:
      - "3000:3000"
    env_file: ../gm-frontend/.env.local
    depends_on:
      - django
```

### 6.2 `Dockerfile` do Next.js

```dockerfile
FROM node:22-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
```

```js
// next.config.ts
const nextConfig = {
  output: "standalone", // necessário para o Dockerfile acima
}
export default nextConfig
```

---

## Checklist de implementação

### Fase 1 — Backend
- [ ] Adicionar `CORS_ALLOW_CREDENTIALS = True` no `settings.py`
- [ ] Atualizar `.env` com origem `http://localhost:3000`

### Fase 2 — Setup Next.js
- [ ] `create-next-app` com as flags corretas
- [ ] Instalar todas as dependências
- [ ] Criar estrutura de pastas

### Fase 3 — Auth
- [ ] Criar client `gm-frontend` no Keycloak Admin
- [ ] Criar `src/lib/auth.ts`
- [ ] Criar `src/app/api/auth/[...nextauth]/route.ts`
- [ ] Criar `src/middleware.ts`
- [ ] Criar `src/lib/api.ts`
- [ ] Testar login e token chegando nas requests ao Django

### Fase 4 — Layout
- [ ] `Sidebar.tsx` com `usePathname`
- [ ] `Topbar.tsx` com sessão do usuário
- [ ] `painel/layout.tsx` protegido

### Fase 5 — Seções
- [ ] Dashboard
- [ ] Contabilidade
- [ ] Clientes
- [ ] Perfis
- [ ] Conciliador

### Fase 6 — Deploy
- [ ] `Dockerfile` do Next.js
- [ ] `docker-compose.yml` atualizado
- [ ] Variáveis de produção configuradas
- [ ] Remover views HTML do Django

---

## Pontos de atenção

**Upload de PDFs no Conciliador**  
O endpoint `/api/extrato-preview/` recebe `multipart/form-data`. No Next.js, usar `FormData` nativo — sem `Content-Type: application/json`.

**Token refresh automático**  
O NextAuth v5 tem refresh automático via `jwt` callback. Configurar `token.expiresAt` e lógica de refresh com o `refreshToken` do Keycloak.

**Combobox de Plano de Contas**  
As listas têm ~1000 itens. Usar virtualização no dropdown ou limitar a 60 resultados filtrados (igual ao JS atual).

**Cache de dados**  
O React Query mantém as listas de plano de contas e históricos em cache — não precisa mais do `window.__GM_PLANO_CONTAS__`.

**Keycloak Client Secret**  
O client `gm-frontend` precisa ser `confidential` (tem client secret) para o NextAuth funcionar no servidor. Diferente do client atual que pode ser `public`.

---

*Criado em: 01/05/2026*
