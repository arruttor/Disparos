# CLAUDE.md — Automação de Clínica Odontológica

Contexto do projeto para sessões do Claude Code (ou para quem assumir o trabalho). **Leia este arquivo inteiro antes de qualquer alteração.** Ele é atualizado ao final de toda sessão que mexe no código — a seção [12](#12-histórico-de-sessões) diz o que foi feito e quando.

> **Regra do responsável pelo projeto:** o Claude **nunca** executa comandos que alterem o git (init, add, commit, push, branch, reset…). Commits são feitos manualmente pelo responsável. Pode sugerir a mensagem de commit; não pode rodar. Leitura (`git status`, `git diff`, `git check-ignore`) é permitida.
>
> **Regra de documentação:** a cada sessão que alterar código, atualize **este arquivo** e o **`DEPLOY.md`**.

---

## 1. O que é o sistema

Sistema de **atendimento automatizado por WhatsApp com IA + agendamento no Google Agenda** para clínicas odontológicas, com um painel de gestão.

**Contexto de negócio**

- Desenvolvido por um dev independente para **vender a um cliente**: uma clínica com **duas unidades** — **Recife** (vários dentistas; a secretária atende o WhatsApp) e **Minas** (1 dentista, que atende o próprio WhatsApp; terá secretária no futuro).
- Atendimento **somente particular** (sem convênio).
- Plano de longo prazo: **revender para outras clínicas** → o sistema já nasce com `Clínica → Unidades → Dentistas` e com o nome da clínica vindo do banco (white-label).

**Escopo da v1** (definido em 01/10/2026)

1. A IA conversa com o paciente em linguagem natural (**sem menus**), identifica o procedimento e a urgência, consulta o Google Agenda e marca — ou sugere horários próximos se o desejado estiver ocupado.
2. Quando a IA não souber ou o caso for crítico, **transfere para um humano**. A equipe continua usando o WhatsApp Business no celular normalmente (a Evolution entra como "dispositivo conectado").
3. **Confirmação D-1** com status visível no próprio Google Agenda (cor + prefixo no título: ⏳ aguardando / ✅ confirmado).
4. **Painel do superusuário**: conectar as APIs (WhatsApp por QR code, Google por OAuth, IA por chave), ver status de cada uma e uma **tela de logs** para achar erros rápido.

**Fora da v1:** integração com o *Dentista Organizado* (software que o cliente usa, sem API pública — planejado via scraping), relatórios, CRM completo.

**Regras de negócio já decididas**

- **Preços nunca são informados pela IA** — somente na avaliação com o dentista.
- Procedimentos, horários e urgências estão **genéricos** (ver `core/management/commands/dados_iniciais.py`) até o cliente enviar os reais.
- Uma conta Google por unidade; recomenda-se **um calendário por dentista** (`Dentista.google_calendar_id`).

---

## 2. Estado atual e roteiro

| # | Etapa | Status |
|---|---|---|
| 1 | Base: modelos, painel, logs, cliente da Evolution, webhook | ✅ 01/10/2026 |
| 2 | Conexão real com WhatsApp (Docker + Evolution) | ✅ 01/10/2026 — Minas conectado e recebendo |
| — | Auditoria de segredos + `.gitignore` | ✅ 01/10/2026 |
| — | Redesign do front-end (design system próprio, sem Bootstrap) | ✅ 01/10/2026 |
| 3 | OAuth do Google Agenda (conectar conta, escolher calendário, status) | ⏳ **próxima** |
| 4 | Robô com IA (Claude + ferramentas: buscar paciente, disponibilidade…) | ⬜ |
| 5 | Agendamento: criar / remarcar / cancelar, cores e prefixos no evento | ⬜ |
| 6 | Transferência para humano (status, aviso para a secretária) | ⬜ — pausa automática já existe |
| 7 | Confirmação D-1 (Celery beat) | ⬜ |
| 8 | Deploy na VPS (Hostinger) | ⬜ — ver `DEPLOY.md` |

**Por que Google antes da IA:** as ferramentas da IA dependem de consultar e criar eventos na agenda; com a agenda pronta, a IA já nasce funcionando de ponta a ponta.

**Para a etapa 3 o responsável precisa criar no Google Cloud Console:** projeto → ativar *Google Calendar API* → tela de consentimento OAuth (modo teste, adicionar o próprio e-mail como testador) → credencial *OAuth Client ID* do tipo **Aplicativo Web** com redirect `http://localhost:8000/integracoes/google/callback/` (rota **planejada**, ainda não existe — confirmar ao implementar; e a URL de produção depois). Client ID/secret são colados em **Integrações → Google Agenda**.

---

## 3. Stack e ambiente

| Item | Valor |
|---|---|
| Python | 3.14 (local, Windows 11) |
| Django | 6.1 |
| Banco | SQLite em dev; PostgreSQL em produção (basta definir `DATABASE_*` no `.env`) |
| Front-end | Django Templates + CSS/JS próprios — **sem build, sem framework, sem Bootstrap** |
| Libs front (CDN) | Google Fonts (Geist, Geist Mono), Phosphor Icons 2.1.1 (unpkg) |
| WhatsApp | Evolution API **v2.3.7** (imagem `evoapicloud/evolution-api:v2.3.7`) em Docker |
| IA | Anthropic Claude — modelo padrão `claude-haiku-4-5` (configurável no painel) |
| Segredos | `.env` (python-dotenv) + campos criptografados no banco (Fernet, `core/fields.py`) |
| Docker | Docker Desktop (instalação **por usuário**; o `docker.exe` fica em `%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin`, que pode não estar no PATH de shells antigos) |

### Comandos

```bash
.venv\Scripts\python manage.py runserver 0.0.0.0:8000
```

> `0.0.0.0` é necessário: a Evolution, dentro do Docker, chama o Django por `host.docker.internal:8000`.

```bash
docker compose -f docker-compose.evolution.yml --env-file .env up -d
```

```bash
.venv\Scripts\python manage.py test
```

```bash
.venv\Scripts\python manage.py dados_iniciais
```

> Idempotente. Cria clínica de exemplo, unidades Recife e Minas, 1 dentista, 8 procedimentos genéricos (incluindo "Urgência") e horários seg–sex 8–12/14–18 + sáb 8–12.

```bash
.venv\Scripts\python manage.py makemigrations --check --dry-run
```

**Acesso local:** usuário `admin`; a senha está no `.env` (`DEV_ADMIN_SENHA`). Painel em http://localhost:8000, Evolution em http://localhost:8080 (manager em `/manager`).

---

## 4. Estrutura

```
automação/
├── config/                 # settings (lê .env), urls raiz
├── core/                   # Clínica, Unidade, Dentista, Procedimento, HorarioAtendimento, RegistroLog
│   ├── fields.py           # CampoCriptografado (Fernet) — use para QUALQUER token/chave
│   ├── logging_handlers.py # grava logs no banco (tela Logs)
│   ├── context_processors.py # `clinica` em todo template (nome na marca — white-label)
│   └── management/commands/dados_iniciais.py
├── integracoes/            # Configuracao (singleton), InstanciaWhatsApp, ContaGoogle
│   ├── evolution.py        # ÚNICO ponto de contato com a Evolution (trocar provedor = reescrever só aqui)
│   ├── ia.py               # verificação da chave da Anthropic
│   ├── views.py            # webhook da Evolution
│   └── tests.py            # todos os testes atuais (14)
├── atendimento/            # Paciente, Conversa, Mensagem, Agendamento
│   └── services.py         # registrar_mensagem, robo_pode_responder (pausa quando humano responde)
├── painel/                 # views do superusuário: dashboard, integrações, QR code, logs
├── templates/
│   ├── painel/             # base.html, login, dashboard, integracoes, whatsapp_qrcode, logs, _campos, _marca
│   ├── admin/base_site.html # tema do admin ("Cadastros")
│   └── 404.html
├── static/
│   ├── css/painel.css      # DESIGN SYSTEM — tokens no topo
│   ├── css/admin.css       # remapeia variáveis do admin para a paleta
│   ├── js/painel.js        # tema, toasts, menus, confirmação
│   └── img/favicon.svg
├── docker-compose.evolution.yml
├── .env.example            # modelo do .env (sem valores reais)
├── CLAUDE.md               # este arquivo
└── DEPLOY.md               # procedimento de deploy
```

---

## 5. Fluxos principais

**Mensagem recebida**
`Evolution → POST /integracoes/webhook/evolution/?token=…` → `integracoes/views.py:webhook_evolution` → valida token → identifica a instância (= unidade) → `atendimento/services.py:registrar_mensagem` → cria/acha `Paciente` (por telefone) e `Conversa` aberta → grava `Mensagem`. *(Etapa 4: em seguida, se `robo_pode_responder`, aciona a IA.)*

**Humano assume a conversa**
Mensagem com `fromMe=True` que **não** foi gravada pelo robô (o robô grava o `id_externo` ao enviar) ⇒ foi a equipe pelo celular ⇒ `Conversa.robo_pausado_ate = agora + Configuracao.pausa_robo_minutos` (padrão 120).

**Conectar WhatsApp**
Painel → Conectar → `whatsapp_conectar` cria a instância se não existir, configura o webhook e redireciona para a tela de QR, que consulta `whatsapp_status` a cada 3 s até ficar `conectado`. Eventos `connection.update` do webhook também atualizam o status.

**Logs**
Use `logging.getLogger("clinica.<area>")`. Os loggers `clinica.*` e `django.request` (ERROR) vão para o console **e** para o banco (`RegistroLog`) → tela **Logs**.

---

## 6. Convenções do projeto

- **Idioma:** código, comentários, UI e mensagens em **português-BR**.
- **Views:** funções, não CBVs. Telas do painel usam o decorator `@superusuario` (`painel/views.py`); views que alteram estado têm `@require_POST`.
- **Segredos:** nunca em código. Configuração de servidor → `.env` via `env_obrigatoria()` (o sistema **não sobe** sem eles). Chaves de API cadastradas pelo painel → `CampoCriptografado`. Formulários de segredo usam `FormSegredosMixin` (campo em branco = mantém o valor).
- **Integrações externas:** toda chamada passa por um módulo de `integracoes/` e falha com exceção própria (`ErroEvolution`) que vira log + mensagem amigável. Nunca mostre `str(e)` cru de banco.
- **Front-end / design:**
  - Direção visual "calma clínica": fundo papel quente, **um** acento verde-água (`--accent`), Geist + Geist Mono.
  - **Nunca escreva cor solta** em template ou CSS novo — use os tokens de `static/css/painel.css` (`--accent`, `--ok`, `--warn`, `--danger`, `--surface*`, `--text*`…). Estado (ok/aviso/erro) tem token próprio, não deriva do acento.
  - **Sem `style="…"` nos templates** — crie classe no CSS.
  - Status de integração = classe `status status--<valor do model>` (ponto + texto). Nível de log = `level level--<NIVEL>`.
  - Números, telefones, horários e códigos com `.num` / `.mono` (fonte mono, tabular).
  - Ícones: Phosphor (`<i class="ph ph-nome">`). Não misturar outra biblioteca.
  - Modo claro/escuro: segue o sistema; o usuário troca no menu da conta (salvo em `localStorage`, chave `painel-tema`). Toda cor nova precisa do par escuro.
  - Estados vazios usam o componente `.empty`; ações destrutivas usam `data-confirmar="…"` no `<form>`.
  - Mudou a paleta em `painel.css`? Atualize também `admin.css`.
- **Testes:** em `integracoes/tests.py` (por enquanto). Todo bug corrigido ganha teste.

---

## 7. Armadilhas conhecidas

| Armadilha | Detalhe |
|---|---|
| **Evolution: filtrar instância inexistente dá 404** | `GET /instance/fetchInstances?instanceName=x` devolve **404** (não lista vazia) quando não existe. `instancia_existe()` lista todas e compara `name`. Descoberto contra a API real em 01/10/2026. |
| **Evolution: exclusão é assíncrona** | `DELETE /instance/delete/<nome>` responde antes de remover; uma consulta imediata ainda mostra a instância. |
| **`URLField` recusa nome de serviço Docker** | `http://evolution:8080` não tem TLD e é rejeitado. `Configuracao.evolution_url` é `CharField` com regex `^https?://`. |
| **Postgres só lê a senha na 1ª inicialização** | Trocar `EVOLUTION_DB_PASSWORD` no `.env` com o volume já criado **quebra** a Evolution. Para trocar: `ALTER USER evolution WITH PASSWORD '…'` no container e depois `up -d`. |
| **Django precisa escutar em `0.0.0.0`** e aceitar `host.docker.internal` | Senão o webhook da Evolution não chega. `DJANGO_ALLOWED_HOSTS` inclui `host.docker.internal`; `URL_PUBLICA=http://host.docker.internal:8000` em dev. |
| **Token do webhook vai na query string** | Aparece no log de acesso do runserver/nginx. Aceitável em dev; em produção mover para header (pendência no `DEPLOY.md`). |
| **Perder `CHAVE_CRIPTOGRAFIA` = perder as chaves salvas** | Os campos criptografados viram vazio (`InvalidToken` → `""`). Guarde a chave fora do servidor. |
| **Logs contêm telefone de paciente** | Ex.: "Novo paciente pelo WhatsApp: … (55…)". Dado pessoal (LGPD) — a tabela `RegistroLog` precisa de política de retenção antes de produção. |
| **Reverse OneToOne inexistente no template** | `u.whatsapp` / `u.conta_google` sem registro **não quebra** o template (`ObjectDoesNotExist` é silenciado pelo Django) — por isso os `{% if u.whatsapp %}`. Em Python, use `getattr(unidade, "whatsapp", None)`. |
| **No admin do Django, `--accent` é a cor da marca no cabeçalho** | Não é cor de destaque genérica. Valor escuro ali deixa "Cadastros" ilegível no header. |
| **Admin e painel têm temas independentes** | O admin usa o próprio seletor (`localStorage.theme`); o painel usa `painel-tema`. |
| **PowerShell + `manage.py shell` via pipe** | O pipe insere BOM (`U+FEFF`) e quebra a 1ª linha. Use `manage.py shell -c "exec(open(r'arquivo.py', encoding='utf-8-sig').read())"`. |
| **PowerShell achata arrays de 1 elemento** | `@(@('a','b'))` vira `@('a','b')`. Num script de substituição isso trocou todo `<` de três templates por uma letra (01/10/2026, corrigido). Para editar arquivos, prefira a ferramenta de edição a scripts. |
| **Projeto está dentro do OneDrive** | `.env` e `db.sqlite3` (com dados de pacientes) sincronizam com a nuvem. Recomendado mover para fora (ex.: `C:\projetos\clinica`). |

---

## 8. Decisões tomadas (e por quê)

| Decisão | Motivo |
|---|---|
| Evolution API (não oficial) em vez da Cloud API da Meta | Gratuita e permite a equipe continuar no celular. Volume do cliente é baixo (risco de banimento baixo). Isolada em `integracoes/evolution.py` para trocar se preciso. |
| SQLite em dev, Postgres em prod | Rodar sem instalar nada além do Python; troca só por `.env`. |
| Sem Celery por enquanto | Só será necessário na etapa 7 (lembretes D-1). |
| Admin do Django para cadastros | Unidades/dentistas/procedimentos mudam pouco; telas próprias depois, se o cliente pedir. Tema aplicado para não parecer outro sistema. |
| Sem Bootstrap; CSS próprio | Identidade própria (produto será revendido), sem cara de template, sem build. |
| Nome da clínica vindo do banco | White-label para revenda. |
| `DEBUG` desligado por padrão e segredos obrigatórios | Evitar subir em produção com chave pública do código ou página de erro exposta. |

---

## 9. Pendências com o cliente

- Como o Google Agenda está organizado hoje (um calendário por dentista? por unidade?).
- Lista real de procedimentos, duração e quem faz cada um.
- Horários reais de cada dentista/unidade.
- Critérios de urgência e o que fazer (encaixe? transferir na hora?).
- FAQ (endereço, estacionamento, formas de pagamento…).
- Orçamento mensal aceitável (VPS + IA).

---

## 10. Ideias para depois (backlog)

- Tela de **Conversas** no painel (ler histórico, assumir/devolver ao robô) — hoje só pelo admin.
- **Lembrete de retorno** (ex.: limpeza a cada 6 meses) e campanhas de reativação de pacientes sumidos.
- **Pesquisa de satisfação** pós-consulta pelo WhatsApp.
- Áudio do paciente → transcrição antes de mandar para a IA.
- Lista de espera: avisar quando abrir horário por cancelamento.
- Relatórios: taxa de faltas, conversas por dia, procedimentos mais pedidos, tempo de resposta.
- Integração com Dentista Organizado (scraping) para importar pacientes/histórico.
- Multi-clínica real (SaaS) com isolamento por clínica, se a revenda acontecer.
- Rotação automática do `RegistroLog` (apagar INFO após X dias).

---

## 11. Antes de finalizar qualquer sessão

1. `python manage.py test` passando.
2. `python manage.py makemigrations --check --dry-run` sem pendências.
3. Teste novo para bug corrigido.
4. Nenhum segredo em código; nenhuma cor solta ou `style=""` em template.
5. Mudança visual verificada no navegador (claro **e** escuro; e em 375 px de largura).
6. **Atualizar este arquivo** (estado, armadilhas, histórico) **e o `DEPLOY.md`**.
7. Sugerir mensagem de commit ao responsável — **não** commitar.

---

## 12. Histórico de sessões

### 01/10/2026
- Levantamento de requisitos e escopo da v1.
- **Etapa 1:** projeto Django (apps `core`, `integracoes`, `atendimento`, `painel`), modelos, painel, logs no banco, cliente da Evolution, webhook, criptografia de segredos, 14 testes.
- **Etapa 2:** Docker + Evolution v2.3.7; correção de `instancia_existe` (404 da API real); WhatsApp de Minas conectado e recebendo.
- **Auditoria de segredos:** removidos defaults inseguros de `SECRET_KEY`/`WEBHOOK_TOKEN`; `DEBUG` passa a ser `False` por padrão; senha do Postgres da Evolution saiu do compose para o `.env` (e foi rotacionada); `.gitignore` robusto.
- **Redesign do front-end:** Bootstrap removido; design system próprio (`static/css/painel.css`), modo escuro, Phosphor Icons, nome da clínica dinâmico, estados vazios, toasts, tela de login nova, tema no admin, 404. Filtros `painel_tags` removidos (sem uso).
- Criados `CLAUDE.md` e `DEPLOY.md`.
