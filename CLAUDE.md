# CLAUDE.md — Automação de Clínica Odontológica

> **Atualização de escopo em 03/11/2026:** o produto agora faz somente disparos iniciais pela Evolution a partir de planilha. IA, Google, lembretes e caixa de entrada ficam desativados. A documentação vigente é a seção inicial de `AGENTS.md` e `DEPLOY.md`; o conteúdo abaixo permanece como histórico. Reservas de número/paciente são permanentes, antes da rede, sem nenhuma repetição automática mesmo após falha. Nova tentativa apenas manual. App `disparos`, telas preservando a identidade atual, base local de 1.773 pacientes de Recife importada e fila pausada.

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
| 3 | OAuth do Google Agenda (conectar conta, escolher calendário, status) + cálculo de horários livres | ✅ 01/10/2026 — validado com conta real: evento criado no Google some da lista de horários livres |
| 4 | Agendamento: marcar / confirmar / remarcar / cancelar no Google, cores e prefixos no evento | 🔧 01/10/2026 — código pronto (55 testes); **falta o responsável testar com a conta real** |
| 5 | Robô com IA (ferramentas: horários livres, marcar, remarcar, cancelar, transferir) + simulador no painel | ✅ 05/10/2026 — validado pelo responsável: responde pelo WhatsApp com o Gemini |
| 6 | Transferência para humano + **caixa de entrada** (menu Conversas) | 🔧 05/10/2026 — código pronto (119 testes) e conferido no navegador (desktop e celular); **falta o responsável testar** responder/assumir/devolver com o WhatsApp real |
| — | Tela de **Consultas** no menu (hoje a lista fica em Integrações → Google) | ⬜ — sugerido |
| — | Roteiro do CRM depois da v1 (funil de orçamento, comparecimento, retorno programado, relatórios, multi-clínica) | ⬜ — fases A–D discutidas em 05/10/2026; priorizar com o cliente |
| 7 | Confirmação D-1 + lembrete do dia + fila de tarefas (Celery + Redis) | 🔧 05/10/2026 — código pronto (148 testes); worker validado no Windows (recebeu e executou tarefa). **Falta o responsável testar** com uma consulta marcada para o dia seguinte |
| 8 | Deploy na VPS (Hostinger) | ⬜ — ver `DEPLOY.md` |
| 9 | **CRM de reativação** com a base do Dentista Organizado (prioridade do cliente desde a 1ª reunião) | 🔎 05/10/2026 — DO mapeado (seção 5, "Dentista Organizado"); importador ainda não escrito |

**Por que Google e agendamento antes da IA:** as ferramentas da IA dependem de consultar e criar eventos na agenda. Em 01/10/2026 a ordem original (IA na 4, agendamento na 5) foi **invertida** a pedido do responsável: a escrita na agenda é validada sozinha pelo painel; a IA nasce só com a parte da conversa, chamando funções já testadas.

**Para a etapa 3 o responsável precisa criar no Google Cloud Console:** projeto → ativar *Google Calendar API* → tela de consentimento OAuth (tipo Externo; adicionar o próprio e-mail como usuário de teste) → credencial *OAuth Client ID* do tipo **Aplicativo Web** com a URI de redirecionamento `http://localhost:8000/integracoes/google/callback/` (o painel mostra a URI exata em Integrações → Google Agenda; em produção, cadastrar também a de produção). Client ID/secret são colados em **Integrações → Google Agenda**.

> ⚠️ Com a tela de consentimento em **modo "Teste"**, o Google **expira o refresh token em 7 dias** — a conta aparece como "Desconectado" e precisa ser reconectada. Para produção, publicar o app ("Em produção"). Como o escopo de agenda é sensível, o Google mostra o aviso "app não verificado" até passar pela verificação — funciona para até 100 usuários, suficiente para a clínica.

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
| IA | **Provedor escolhido no painel** (Integrações → Assistente de IA): **Anthropic** (SDK `anthropic` 1.x, padrão `claude-opus-5-5`, `effort: medium`, `fallbacks: "default"`), **Google Gemini** (SDK `openai` no endpoint compatível do Google, padrão `gemini-3.8-flash`, `reasoning_effort: low`) ou **outro compatível com OpenAI** (Groq, OpenRouter… com URL própria). **Em uso nos testes desde 01/10/2026: Gemini (plano gratuito)** |
| Fila de tarefas | **Celery 5.6 + Redis** (opcional — `CELERY_BROKER_URL`). Redis da fila no mesmo compose da Evolution (`fila-redis`, só em 127.0.0.1:6379). Sem fila ou sem worker vivo: assistente por thread, lembretes pelo botão/comando |
| Segredos | `.env` (python-dotenv) + campos criptografados no banco (Fernet, `core/fields.py`) |
| Docker | Docker Desktop (instalação **por usuário**; o `docker.exe` fica em `%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin`, que pode não estar no PATH de shells antigos) |

### Comandos

**Tudo de uma vez** (Docker + Django + worker + beat, cada um numa janela):

```bash
powershell -ExecutionPolicy Bypass -File .\iniciar_dev.ps1
```

Ou cada parte separada:

```bash
.venv\Scripts\python manage.py runserver 0.0.0.0:8000
```

```bash
.venv\Scripts\celery -A config worker -P threads -c 4 -l info
```

```bash
.venv\Scripts\celery -A config beat -l info
```

> No Windows o worker **precisa** de `-P threads` (o pool padrão não funciona) e o beat roda **separado** (`-B` não é suportado no Windows).

```bash
.venv\Scripts\python manage.py enviar_lembretes
```

> Envia confirmações/lembretes pendentes uma vez (respeita a janela 8h–20h). Alternativa ao beat (cron/Agendador de Tarefas).

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
├── config/                 # settings (lê .env), urls raiz, celery.py (app da fila)
├── core/                   # Clínica, Unidade, Dentista, Procedimento, HorarioAtendimento, RegistroLog
│   ├── fields.py           # CampoCriptografado (Fernet) — use para QUALQUER token/chave
│   ├── logging_handlers.py # grava logs no banco (tela Logs)
│   ├── context_processors.py # `clinica` em todo template (nome na marca — white-label)
│   └── management/commands/dados_iniciais.py
├── integracoes/            # Configuracao (singleton), InstanciaWhatsApp, ContaGoogle
│   ├── evolution.py        # ÚNICO ponto de contato com a Evolution (trocar provedor = reescrever só aqui)
│   ├── google.py           # ÚNICO ponto de contato com o Google: OAuth (REST, sem lib), renovação de token, calendarList, freeBusy
│   ├── ia.py               # provedores de IA: URL do Gemini, modelo padrão por provedor, verificação da chave, mensagens de erro
│   ├── views.py            # webhook da Evolution
│   └── tests.py            # testes de webhook, Evolution, Google (OAuth + agenda), painel
├── atendimento/            # Paciente, Conversa, Mensagem, Agendamento
│   ├── agenda.py           # horários livres: calcular_horarios_livres (pura) + buscar_horarios_livres (usa Google)
│   ├── agendamentos.py     # marcar / confirmar / cancelar / remarcar — banco + Google na mesma transação (a IA chama estas)
│   ├── assistente.py       # IA: prompt (fixo + do turno), 8 ferramentas, histórico; ciclo _turno_anthropic e _turno_compativel (Gemini…)
│   ├── services.py         # registrar mensagem, eco, enviar (assistente/equipe), transferir, assumir/devolver/encerrar, nota, disparar turno
│   ├── signals.py          # mantém o resumo da última mensagem em Conversa (caixa de entrada rápida)
│   ├── lembretes.py        # confirmação D-1, lembrete do dia, "SIM" do paciente (executar / tratar_resposta_confirmacao)
│   ├── tasks.py            # tarefas Celery: processar_turno (assistente) e enviar_lembretes (beat, a cada 5 min)
│   ├── management/commands/enviar_lembretes.py
│   └── tests.py            # testes de disponibilidade, agendamento e telefone
├── painel/                 # views do superusuário: dashboard, conversas (caixa de entrada), integrações, QR code, Google (OAuth/calendários), consultas, assistente/simulador, logs
├── templates/
│   ├── painel/             # base.html, login, dashboard, integracoes, whatsapp_qrcode, google_unidade, logs, _campos, _marca
│   ├── admin/base_site.html # tema do admin ("Cadastros")
│   └── 404.html
├── static/
│   ├── css/painel.css      # DESIGN SYSTEM — tokens no topo
│   ├── css/admin.css       # remapeia variáveis do admin para a paleta
│   ├── js/painel.js        # tema, toasts, menus, confirmação
│   └── img/favicon.svg
├── docker-compose.evolution.yml # Evolution (+ Postgres e Redis dela) e o Redis da fila do Django (dev)
├── iniciar_dev.ps1         # sobe Docker + Django + worker + beat (Windows)
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

**Conectar Google Agenda**
Painel → Conectar (POST `google_conectar`) → gera `state` assinado (`django.core.signing`, 10 min) com a unidade + um nonce guardado na sessão → Google → `GET /integracoes/google/callback/` (`google_callback`) confere assinatura **e** nonce da sessão → troca o código por tokens → exige refresh token e o escopo de agenda → salva em `ContaGoogle.token_json` (criptografado) → página `google_unidade` para escolher o calendário de cada dentista. O calendário principal é gravado como vazio (`""` ⇒ `"primary"`).

**Horários livres** (`atendimento/agenda.py`)
Janelas de `HorarioAtendimento` do dia − intervalos ocupados (Google `freeBusy` + `Agendamento` não cancelados do próprio sistema) − antecedência mínima (120 min) → inícios a cada 30 min em que a consulta inteira cabe. Se o Google não responde, **lança `ErroGoogle`** — nunca oferecer horário sem conferir a agenda. Constantes `PASSO_MINUTOS`, `ANTECEDENCIA_MINUTOS`, `MAX_DIAS` no topo do módulo.

**Agendamento** (`atendimento/agendamentos.py`)
- `marcar()` confere de novo se o horário está livre (Google + banco) → cria `Agendamento` e o evento no Google **na mesma transação** — se o Google falhar, nada é gravado.
- `confirmar()`, `cancelar()`, `remarcar()` gravam e chamam `_sincronizar()` (PATCH do evento) também em transação.
- Como cada status aparece no Google (`VISUAL`): ⏳ amarelo (cor 5) aguardando · ✅ verde (10) confirmado · ❌ vermelho (11) cancelado. O evento carrega `extendedProperties.private.agendamento_id`.
- **Cancelado continua visível** no Google, mas com `transparency: transparent` — o freeBusy ignora, então o horário volta a ser oferecido.
- **Remarcar** volta o status para "aguardando" (precisa de nova confirmação e novo lembrete). O horário antigo da própria consulta conta como livre (`ignorar=` + `subtrair_intervalo`).
- O calendário usado fica gravado em `Agendamento.google_calendar_id` — se o dentista trocar de calendário, as atualizações continuam indo para o evento original.
- Erros de regra viram `ErroAgendamento` com mensagem pronta para o usuário (e, na etapa 5, para o paciente).
- No painel: **Integrações → Google → Calendários e teste** — os horários livres são botões que marcam uma consulta de teste; a lista "Próximas consultas" tem confirmar / remarcar / cancelar.

**Assistente de IA** (`atendimento/assistente.py` + `services.py`)
1. Webhook grava a mensagem do paciente → `disparar_assistente()` abre uma **thread** (não segura o webhook).
2. Espera `tempo_de_espera()` = `Configuracao.espera_resposta_segundos` (padrão 8 s), **+12 s se a mensagem é só um cumprimento** ("oi", "boa tarde" — `CUMPRIMENTOS` em `services.py`). Cada mensagem agenda o próprio turno; só o da mensagem **mais nova** do paciente segue (checado antes da trava e de novo ao conseguir a trava) — e responde todas juntas (o histórico junta mensagens seguidas).
3. Trava por conversa (um turno por vez) → `robo_pode_responder()` → `responder()`.
4. `responder()`: histórico das últimas 72 h (máx. 40 mensagens, **só texto**, começando por mensagem do paciente; mensagens da equipe entram como assistant com o prefixo "[Mensagem enviada pela equipe da clínica]") → `client.beta.messages.create` com `system` em 2 blocos (fixo com `cache_control` + do turno: data/hora, unidade, dentistas, paciente e consultas) → ciclo de ferramentas (máx. 8 idas) → texto final.
4b. **Resposta velha é descartada:** se o paciente mandou mensagem nova enquanto a IA gerava, a resposta não é enviada e o turno da mensagem nova responde tudo — **exceto** se a IA já fez uma ação (marcou/remarcou/cancelou/confirmou) ou transferiu, aí a resposta sai.
5. `enviar_resposta()` grava a mensagem do robô com `id_externo = "pendente:<uuid>"` **antes** de enviar; o eco `fromMe` do webhook é reconhecido pelo texto (janela de 2 min) e não pausa o robô.
6. Falhou (chave, créditos, rede, erro inesperado)? O paciente recebe `MENSAGEM_FALHA`, a conversa vai para a equipe e o erro vai para os Logs.
- **Ferramentas** (todas `strict: true`): `buscar_horarios_livres`, `marcar_consulta`, `listar_consultas_do_paciente`, `confirmar_consulta`, `remarcar_consulta`, `cancelar_consulta`, `salvar_nome_paciente`, `transferir_para_humano`. Só agem nas consultas **do paciente da conversa**. Erros de regra voltam ao modelo como `tool_result` com `is_error`.
- **Regras fixas no prompt** (não configuráveis): nunca informar preço, nunca inventar horário, confirmar antes de marcar/remarcar/cancelar, sem diagnóstico, urgência/gravidade, quando transferir, somente particular.
- **Configurável** (painel → Assistente): nome, tratamento (você/senhor), informações extras (FAQ), espera, pausa do robô.
- **Simulador** (painel → Assistente): conversa com `Conversa.simulada=True` e paciente fictício `sim<id do usuário>`; nada vai pelo WhatsApp, mas **consultas marcadas vão para o Google Agenda de verdade**.

**Caixa de entrada** (menu **Conversas** — `painel/views.py` + `templates/painel/conversas.html`)
- Lista à esquerda com abas **Com a equipe / Com o assistente / Encerradas / Todas**, filtro de unidade e busca (nome ou telefone). Conversas simuladas não aparecem. Badge no menu = conversas com a equipe cuja última mensagem é do paciente (`Conversa.aguardando_resposta`, contado no context processor).
- Conversa à direita: **Assumir** (status `humano`, `atendente` = usuário), **Devolver ao assistente** (status `robo`, limpa atendente/pausa), **Encerrar** (próxima mensagem do paciente abre conversa nova com o assistente). **Responder** envia pelo WhatsApp da unidade (`enviar_mensagem_equipe`) e quem responde **assume** a conversa. **Nota interna** (`Mensagem.Origem.NOTA`) só a equipe vê — nunca vai ao paciente nem à IA.
- Atualização sem recarregar: polling de 4 s na conversa aberta (`conversa_atualizacoes?depois=<id>`) e de 10–15 s na lista (`conversas_assinatura`); a página só recarrega se o usuário não estiver digitando.
- Resumo da última mensagem fica em `Conversa.ultima_mensagem_*`, mantido pelo signal `atendimento/signals.py` (nota não altera).

**Confirmação D-1 e lembrete do dia** (`atendimento/lembretes.py`)
- `executar()` roda a cada 5 min (beat) — ou pelo botão **Assistente → Confirmação e lembretes → Enviar pendentes agora** (envio manual: ignora a janela, a hora configurada e as carências de "acabou de marcar"), ou `manage.py enviar_lembretes`. O automático só envia entre **8h e 20h**.
- **Pedido de confirmação:** consultas de **amanhã**, status "aguardando", ainda sem pedido, marcadas há mais de 2 h, a partir de `Configuracao.confirmacao_hora` (padrão 10:00). Marca `Agendamento.lembrete_enviado_em`.
- **Lembrete do dia:** `lembrete_horas_antes` (padrão 2; 0 desliga) antes da consulta, para aguardando/confirmadas, se a consulta foi marcada antes dessa janela. Marca `aviso_dia_enviado_em`.
- As mensagens entram na conversa do paciente como mensagens do assistente (a IA vê o contexto). Falha no envio não marca nada → tenta de novo na próxima rodada. Pacientes do simulador (telefone não numérico) são pulados.
- **Resposta "SIM"** (`tratar_resposta_confirmacao`, chamado no webhook **antes** do assistente): se `eh_confirmacao(texto)` e há pedido enviado nas últimas 36 h para consulta futura aguardando → `agendamentos.confirmar()` (✅ no Google) + resposta "Consulta confirmada ✅". Funciona com o assistente desligado. Qualquer outra resposta segue para o assistente (regra 10 do prompt) ou para a equipe.

**Fila de tarefas** (`config/celery.py`, `atendimento/tasks.py`)
- `disparar_assistente()`: com `CELERY_BROKER_URL` **e** worker vivo (`celery_tem_worker()`, ping com cache de 30 s) → `processar_turno.apply_async(countdown=espera)`; senão → thread. Redis fora do ar → thread.
- `CELERY_TASK_ACKS_LATE`: a tarefa só sai da fila depois de concluída (sobrevive a reinício do worker).

**Dentista Organizado — mapeamento** (05/10/2026, só leitura, com o login do responsável; autorização da clínica)
- Sistema Java/Struts antigo (`https://www.dentistaorganizado.com.br/*.do`), sessão por cookie, **sem API e sem exportação de verdade** — o "Exportar Excel" dos relatórios só converte a tabela da tela no navegador. Tudo sai de **GET que devolvem HTML** → parsear `<table>`.
- **Pacientes:** `listaContatosMiolo.do?p2=100000&exibirLista=1` (fragmento AJAX; `p2` = tamanho da página) → todos numa chamada (~1.760). Colunas: ID interno, nome, grupo, prontuário, celular, situação. Links usam `contato.contatoId` (chave para casar tudo).
- **Ficha:** `editarContato.ft?contato.contatoId=N` — campos `contato.*` (nome, cpf, telefone, foneResidencial, email, sexo, endereço, indicação, obs, situação…).
- **Consultas:** `relatorioConsultas.do?data1=dd/mm/aaaa&data2=dd/mm/aaaa&btnokbusca=1` → paciente (`mostraResumo('<contatoId>')`), telefone, cadastro, data/hora (`dd/mm/aa hh:mm`), dentista, status (Agendada/Confirmada/Finalizada/Faltou/Cancelada/Chegou/Em atendimento). ~4.300 linhas, ~8 MB, 8 s. **Quase tudo é de 2021–2023** — a clínica parece ter parado de usar a agenda do DO (confirmar com o cliente).
- **Procedimentos:** `relatorioTratamentos.do?data1=…&data2=…&btnokbusca=1` → paciente, telefone, procedimento, data de criação, data de finalização, evoluções, status (Concluído/Andamento/Cancelado). ~6.200 linhas de 2019 a 2026, **36 s** numa chamada — a fonte mais atual de "última vez que veio". **Status não é confiável:** a equipe não fecha os tratamentos no sistema (confirmado em 06/10/2026), por isso ~3.400 ficam em "Andamento". A atividade do paciente sai das **datas** (criação/finalização do procedimento, consultas, evoluções), nunca do status.
- **Conta do DO = só unidade Recife** (06/10/2026).
- Outros: `relatorioConsultasAtendidasSemRetorno.do` e `relatorioConsultasRechamadas.do` (`dataSearchInicio/Fim`), `findAllOrcamentos.do` (`data1/data2`, orçamentos com status), `alertasRetorno.do`.
- **Cuidados para o importador:** fatiar por ano (não sobrecarregar o servidor deles), pausa entre chamadas, só GET, HTML em ISO-8859-1/UTF-8 misturado (acentos quebram), datas com ano de 2 dígitos. Login do DO cadastrado pelo painel em `CampoCriptografado`.

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
  - Estados vazios usam o componente `.empty`; ações destrutivas usam `data-confirmar="…"` no `<form>` — abre o diálogo próprio do painel (`#dialogo-confirmar` no `base.html`, lógica em `painel.js`). **Nunca use `window.confirm`/`alert`/`prompt`** (ver armadilhas). O rótulo do botão de confirmar é o texto do botão que foi clicado.
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
| **Django fora do ar = webhooks perdidos** | A Evolution não reenvia eventos que falharam. Em 01/10/2026 o servidor caiu e o evento "open" se perdeu: o painel mostrou Minas como "Aguardando" estando conectado. Status se corrige com **Verificar status**; mensagens perdidas não voltam. Em produção, o processo web precisa de reinício automático (e, futuramente, uma tarefa periódica de verificação). |
| **Rajadas de `connection.update`** | Ao reconectar, a Evolution manda dezenas de "connecting" em segundos. O webhook só loga quando o status muda de fato. |
| **Duas URLs: `URL_PUBLICA` ≠ `URL_SITE`** | `URL_PUBLICA` = como a **Evolution** chama o Django (webhook, rede interna). `URL_SITE` = como o **navegador** abre o painel (retorno do OAuth). O Google só aceita `http://localhost` ou HTTPS — `host.docker.internal` não serve para o OAuth. |
| **Google em modo "Teste" expira o refresh token em 7 dias** | Ver seção 2. O sintoma é a conta virar "Desconectado" com a mensagem "Conecte a conta novamente". |
| **"Error 403: access_denied — app is currently being tested"** | Não é bug do sistema: em modo "Teste", só contas cadastradas em **Google Auth Platform → Público-alvo → Usuários de teste** podem autorizar. O Google mostra o erro na própria tela dele e **não volta** para o callback (nada aparece nos Logs). Adicionar o e-mail exato da conta e conectar de novo. Visto em 01/10/2026. |
| **`prompt=consent` é obrigatório no OAuth** | Sem ele, o Google só manda refresh token na **primeira** autorização da conta; reconectar depois viria sem refresh token. `trocar_codigo` recusa resposta sem refresh token. |
| **Dentista sem `HorarioAtendimento` = nenhum horário livre** | Sem janela de atendimento não há o que calcular. A tela de teste avisa ("não tem horário de atendimento") e a lista de calendários marca o dentista. Dentista criado pelo admin **não** ganha horários automaticamente. Visto em 01/10/2026 (dentista "vinicius", Minas). |
| **CSS antigo preso no cache do navegador (dev)** | Em desenvolvimento, mudou o `painel.css` e a tela não mudou? **Ctrl+F5**. Em produção (`DEBUG=false`) isso não acontece: `ManifestStaticFilesStorage` põe o hash no nome do arquivo — mas aí o `collectstatic` é **obrigatório**, senão toda página quebra com `Missing staticfiles manifest entry`. |
| **Mudanças feitas direto no Google Agenda não voltam para o sistema** | Arrastar, editar ou apagar o evento no Google não altera o `Agendamento`. O evento avisa isso na descrição. Um evento movido no Google ainda bloqueia o horário novo (freeBusy) e o sistema continua achando que a consulta está no antigo. Sincronização Google → sistema está no backlog. Cancelar uma consulta cujo evento foi apagado no Google funciona (404/410 é tolerado); confirmar/remarcar avisam para cancelar e marcar de novo. |
| **Telefone brasileiro com e sem o 9** | O WhatsApp ainda manda alguns celulares antigos sem o 9 extra (`553188881111`, 12 dígitos). `normalizar_telefone` aceita os dois formatos — o mesmo paciente pode aparecer com dois telefones se digitado num formato e chegar pelo WhatsApp no outro. |
| **Exceção dentro de `transaction.atomic` deixa o objeto em memória "sujo"** | Em `confirmar/cancelar/remarcar`, se o Google falhar o banco volta, mas o objeto Python continua com o status novo. Quem chama deve descartar o objeto (ou `refresh_from_db()`) depois de `ErroAgendamento`. |
| **`window.confirm` é suprimido em navegadores embutidos** | No painel de navegador do app (e em alguns WebViews) o `confirm()` devolve `false` em 1 ms sem mostrar nada — o botão "Desconectar" cancelava o envio em silêncio e **nada chegava ao servidor** (nem log). Corrigido em 01/10/2026 com diálogo próprio (`<dialog>`). |
| **`pushName` pode vir `null` no webhook** | Contato sem nome no perfil. `dados.get("pushName", "")` devolve `None` (a chave existe) → `IntegrityError` → **mensagem do paciente perdida**. Sempre `dados.get(...) or ""`. Visto em 01/10/2026 (3 mensagens perdidas). |
| **Trocar o número do WhatsApp de uma unidade** | Desconectar (logout na Evolution, a instância continua existindo) → informar o número novo → Conectar. O QR code é pedido com `?number=` do número atual (o código de pareamento é por número). A Evolution aceita logout de instância não conectada (`SUCCESS`); se o celular já saiu sozinho, o painel trata como sucesso. |
| **`display` de componente vence o atributo `hidden`** | `.success-mark { display: grid }` fazia o ícone de "conectado" aparecer antes do QR code. Regra global `[hidden] { display: none !important }` no topo do `painel.css` — não remova. |
| **QR code demora logo após criar a instância** | A 1ª chamada a `/instance/connect` pode vir sem `base64`. A view tenta até `TENTATIVAS_QRCODE` (4) vezes com 1 s de intervalo; se ainda faltar, a tela mostra "Gerando…" e recarrega em 3 s. Com QR, a página se renova a cada 40 s (o código expira). |
| **Gemini exige devolver a `thought_signature`** | Pelo endpoint compatível com OpenAI, cada `tool_call` vem com `extra_content.google.thought_signature`, que precisa voltar **igual** na chamada seguinte — senão 400. O código devolve `message.model_dump(exclude_none=True)` inteiro (o SDK `openai` preserva campos extras). Não "limpe" essa mensagem nem monte o dict à mão. Há teste cobrindo. |
| **Gemini aceita só parte do JSON Schema** | `ferramentas_compativeis()` converte: tira `strict`/`additionalProperties`, campo `["integer","null"]` vira opcional, ferramenta sem parâmetros vai sem `parameters`. Ferramenta nova? Confira o teste `test_ferramentas_no_subconjunto_que_o_gemini_aceita`. |
| **Plano gratuito do Gemini usa as conversas para treino** | Oficial na página de preços do Google. **Só dados fictícios** enquanto estiver no gratuito. Com pacientes reais: Gemini pago, Claude, ou Groq (não treina). O painel mostra um aviso fixo quando o provedor é Gemini. Limites do gratuito ficam no AI Studio; estourou → erro "Limite de uso atingido" nos Logs e a conversa vai para a equipe. |
| **Gemini gratuito dá 503 "high demand" com frequência** | Sobrecarga temporária do Google — não é bug. Defesas: o SDK tenta `TENTATIVAS_API` (4) vezes com espera crescente; se persistir **na 1ª chamada do turno**, usa o **modelo reserva** (`ia_modelo_reserva`, padrão `gemini-3.5-flash`; as cotas do gratuito são por modelo). No meio do turno **não** troca: as `thought_signature` são presas ao modelo que as gerou. Se tudo falhar: mensagem padrão + conversa para a equipe. Visto em 01/10/2026 no 1º teste real pelo WhatsApp. |
| **Conversa transferida por falha temporária fica presa** | Volta pelo admin: Cadastros → Conversas → selecionar → ação **"Devolver ao assistente"**. A mensagem que falhou não é respondida de novo; o paciente precisa mandar outra. |
| **Trocar de provedor no painel ajusta o modelo** | Se o modelo não combina com o provedor (ex.: `claude-…` com Gemini), o formulário troca pelo padrão do provedor (`MODELO_PADRAO` em `integracoes/ia.py`). |
| **Regras da API no Claude Opus 5.5** | Não enviar `thinking` (sempre ligado; `disabled`/`budget_tokens` = 400); não forçar `tool_choice` `any`/`tool` (400) — use `auto` + `strict: true`; sem prefill; esforço padrão é `medium` (enviado explicitamente). Blocos de thinking só podem ser reenviados sem edição — por isso entre turnos o histórico vai **só com texto** e, dentro do turno, `response.content` é devolvido inteiro. Mudou o modelo para outro? Revise estas regras. |
| **Sem worker do Celery, o assistente volta para thread** | Com fila + worker, os turnos sobrevivem a reinício do Django. Sem worker (ou sem `CELERY_BROKER_URL`), volta para thread — e aí reiniciar o servidor (inclusive o autoreload do `runserver`) **mata** turnos em andamento. A trava por conversa é por processo: com vários workers do Celery/gunicorn, duas mensagens quase simultâneas podem gerar dois turnos em paralelo (a checagem "sou a mensagem mais nova?" evita a maioria). |
| **Redis de pé + worker parado = paciente sem resposta** | Por isso `celery_tem_worker()` confere antes de usar a fila. Não remova essa checagem. Atenção ao cache de 30 s: se o worker cair, por até 30 s as tarefas ainda vão para a fila (serão processadas quando ele voltar, com `acks_late`). |
| **Celery no Windows** | Worker só com `-P threads` (o pool `prefork` não funciona no Windows) e beat como processo separado (`-B` não funciona). Em Linux/produção pode usar o padrão. O `.env` só é lido quando o processo inicia — mudou o `.env`, reinicie Django, worker e beat. |
| **Nos testes a fila fica desligada** | `CELERY_BROKER_URL = ""` quando `test` está no `sys.argv` (não depender de Redis). Os testes da fila usam `override_settings` + mocks. |
| **Conversa transferida não volta sozinha para o assistente** | `status = humano` fica até alguém clicar **Devolver ao assistente** (Conversas) ou usar a ação do admin. |
| **Resumo da conversa é mantido por UPDATE direto (signal)** | `Conversa.save()` sem `update_fields` **não grava** os campos `ultima_mensagem_*` (sobrescrita em `Conversa.save`), para um objeto carregado antes da última mensagem não apagar o resumo. Para mexer no resumo, use `Conversa.objects.filter(...).update(...)`. Há teste de regressão. |
| **Mensagem da equipe pelo painel também tem eco** | Igual à do assistente: grava com `id_externo = "pendente:…"` antes de enviar; o eco `fromMe` é casado pelo texto (origem `robo` **ou** `humano`) e não duplica. Falha no envio → `id_externo = "falha:…"` e a bolha aparece com "⚠ não enviada". |
| **Custo por mensagem** | Cada turno manda prompt + ferramentas + histórico; o bloco fixo do prompt usa cache. O log "Assistente respondeu…" registra tokens de entrada, do cache e de saída — use para estimar o custo mensal. |
| **SQLite: "database is locked" (só em dev)** | O SQLite aceita uma gravação por vez; rajadas de `connection.update` da Evolution + painel + assistente gravando juntos estouravam a espera de 5 s (visto em 05/10/2026 — 3 avisos de conexão perdidos). Corrigido com `OPTIONS` do SQLite em `settings.py` (WAL, `transaction_mode: IMMEDIATE`, `timeout: 20`) e com o webhook gravando `connection.update` **só quando o status muda**. Em produção (PostgreSQL) o problema não existe. |
| **Webhook recebe muito mais que texto** | Reações, figurinhas, `protocolMessage` (apagar/editar), `albumMessage` (só anuncia as fotos) e `secretEncryptedMessage` são **ignorados** (`TIPOS_IGNORADOS` em `integracoes/views.py`) — senão um 👍 acionaria a IA. Mídias viram descrição legível (`DESCRICAO_MIDIA`: "📷 Imagem", "🎤 Áudio", "📄 Documento: nome.pdf" + legenda). Tipo desconhecido é registrado como "❔ Mensagem sem texto (tipo)" e logado. O prompt manda o assistente dizer que não abre mídia e pedir texto. Mensagens antigas gravadas como "[imageMessage]" etc. ficaram como estavam. |
| **Rolagem lateral escondida não funciona com mouse** | `overflow-x: auto` + barra escondida só rola no toque. Abas da caixa de entrada **quebram linha** em vez de rolar; o menu (celular) usa `data-rolagem-lateral` (rodinha + arrastar com o mouse + esmaecimento quando há mais itens, em `painel.js`). Faixa horizontal nova? Use um dos dois. |
| **Fuso único `America/Recife` para todas as unidades** | Verificado em 05/10/2026: banco em UTC, telas/JSON/assistente/Google em UTC−3, tudo coerente (a agenda Google da conta está em `America/Sao_Paulo`, mas os eventos levam `timeZone` explícito). Hoje Recife e Minas (São Paulo) têm o mesmo horário. **Se o Brasil voltar a ter horário de verão**, São Paulo/Minas mudam e Recife não — os horários de Minas ficariam 1 h errados. Solução prevista: campo de fuso por `Unidade` (backlog). Ao rodar diagnósticos no shell, use `timezone.localtime()` — `strftime` direto num datetime do banco mostra UTC. |
| **`runserver` órfão segura a porta 8000** | Se o preview do app for parado de forma brusca, um `runserver` antigo pode continuar servindo código velho. Conferir com `Get-NetTCPConnection -LocalPort 8000` e encerrar o PID. |
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
| Celery + Redis a partir da etapa 7, **opcional** | Lembretes precisam de agendador; a fila também protege as respostas do assistente contra reinícios. Opcional para o sistema continuar funcionando sem Redis (dev simples, testes). |
| "SIM" confirma sem passar pela IA | Mais barato, instantâneo, determinístico e funciona com o assistente desligado. Só respostas ambíguas vão para a IA. |
| Admin do Django para cadastros | Unidades/dentistas/procedimentos mudam pouco; telas próprias depois, se o cliente pedir. Tema aplicado para não parecer outro sistema. |
| Sem Bootstrap; CSS próprio | Identidade própria (produto será revendido), sem cara de template, sem build. |
| Nome da clínica vindo do banco | White-label para revenda. |
| `DEBUG` desligado por padrão e segredos obrigatórios | Evitar subir em produção com chave pública do código ou página de erro exposta. |
| Google via REST com `requests` (sem `google-api-python-client`) | Só 5 endpoints são usados; evita 3 dependências pesadas e mantém o padrão do `evolution.py`. |
| Escopo `auth/calendar` (completo) | Precisa listar calendários **e** criar/editar eventos (etapa 5). |
| Gemini gratuito na fase de testes (01/10/2026) | Pedido do responsável (custo zero). Pesquisa: Anthropic e OpenAI não têm API gratuita; Gemini tem gratuito permanente com limites folgados mas usa os dados para treino; Groq não treina mas o limite (8k tokens/min) é apertado demais para o assistente. |
| Provedores via endpoint compatível com OpenAI (não SDK nativo do Google) | Uma integração serve Gemini, Groq, OpenRouter, Mistral… O caminho Claude (SDK oficial) continua intacto. |
| Claude Opus 5.5 como modelo padrão (não Haiku) | Recomendação da Anthropic; o Haiku tinha sido escolha de custo feita por mim sem o responsável decidir. Configurável em Integrações. |
| Histórico reconstruído do banco, só texto | Simples, sobrevive a reinícios e evita a checagem de edição de histórico dos blocos de thinking. |
| Espera de 8 s antes de responder | Paciente costuma mandar várias mensagens seguidas; uma resposta só, que considera tudo. |
| Falha da IA → mensagem padrão + transferência | O paciente nunca fica sem resposta e a equipe fica sabendo. |
| Consulta cancelada fica visível no Google (❌ vermelho, "livre") em vez de apagada | A equipe vê o histórico na própria agenda; `transparent` faz o horário voltar a ser oferecido. |
| Banco + Google na mesma transação | Nunca existir consulta só no sistema ou só no Google por falha nossa. |
| Horários livres conferem também os `Agendamento` locais | Se a criação do evento no Google falhar, o horário não é oferecido de novo para outro paciente. |

---

## 9. Pendências com o cliente

**Para o CRM de reativação (prioridade definida na 1ª reunião):**
*(Fonte de dados resolvida em 05/10/2026: leitura do DO por scraping, autorizada pela clínica — ver seção 5.)*
- **Onde as consultas são marcadas desde 2023?** A agenda do DO quase não tem nada depois de 2023. Google Agenda (um calendário por dentista? nome do paciente no título?), papel, outro sistema?
- ~~Conta do DO é de qual unidade?~~ **Respondido (06/10/2026): só Recife** — as outras unidades ainda estão sendo construídas. Todo paciente importado do DO pertence a Recife.
- ~~Os procedimentos "Andamento" são reais?~~ **Respondido (06/10/2026): não** — a equipe não costuma fechar os tratamentos no sistema. O status do procedimento **não serve** para achar "tratamento parado"; usar datas (ver seção 5).
- Definição de "paciente inativo" (meses sem vir) e se muda por tipo (limpeza 6 meses, orto mensal…).
- Quem **não** deve receber: em tratamento ativo, quem pediu para não ser contatado, conflitos, falecidos; menores → responsável. Pacientes antigos de convênio entram?
- Por qual número saem as mensagens (recomendação: chip novo, não o principal) e quantas por dia.
- O que oferecer e quem aprova o texto (regras de publicidade do CFO — sem preço/promoção).
- Resposta do paciente: a IA marca direto ou passa para a secretária?
- A ficha de cadastro tem autorização de contato? Opt-out em toda mensagem (LGPD).
- Meta de sucesso (pacientes de volta por mês) — para medir e para precificar.
- Um usuário do DO só para o sistema (se o DO permitir permissão só de leitura).

**Da v1:**
- Como o Google Agenda está organizado hoje (um calendário por dentista? por unidade?).
- Lista real de procedimentos, duração e quem faz cada um.
- Horários reais de cada dentista/unidade.
- Critérios de urgência e o que fazer (encaixe? transferir na hora?).
- FAQ (endereço, estacionamento, formas de pagamento…).
- Orçamento mensal aceitável (VPS + IA).

---

## 10. Ideias para depois (backlog)

- **Lembrete de retorno** (ex.: limpeza a cada 6 meses) e campanhas de reativação de pacientes sumidos.
- **Pesquisa de satisfação** pós-consulta pelo WhatsApp.
- Áudio do paciente → transcrição antes de mandar para a IA.
- Lista de espera: avisar quando abrir horário por cancelamento.
- Relatórios: taxa de faltas, conversas por dia, procedimentos mais pedidos, tempo de resposta.
- Integração com Dentista Organizado (scraping) para importar pacientes/histórico.
- Multi-clínica real (SaaS) com isolamento por clínica, se a revenda acontecer.
- Rotação automática do `RegistroLog` (apagar INFO após X dias).
- **Sincronização Google → sistema** (push notifications do Calendar ou varredura periódica pelos `agendamento_id` nas extendedProperties): refletir eventos movidos/apagados direto na agenda.
- Mesclar pacientes duplicados (mesmo celular com e sem o 9).
- **"Digitando…" do WhatsApp:** assinar o evento `PRESENCE_UPDATE` da Evolution e adiar a resposta enquanto o paciente estiver digitando (exige `presenceSubscribe` no contato — avaliar).
- **Fuso horário por unidade** (`Unidade.fuso`): só necessário se voltar o horário de verão ou se entrar unidade em outro fuso (ex.: Manaus, UTC−4).

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

### 05/10/2026
- Etapa 5 validada pelo responsável (assistente respondendo pelo WhatsApp com o Gemini).
- **Etapa 6 — caixa de entrada:** menu **Conversas** com badge; lista com abas/filtro/busca; conversa com assumir, devolver, encerrar, responder pelo painel (vai pelo WhatsApp) e nota interna; atualização por polling; próximas consultas do paciente ao lado; layout de celular (lista ou conversa). Modelos: `Conversa.atendente` + `ultima_mensagem_*`, `Mensagem.autor` + origem `nota`, rótulos de status ("Com o assistente" / "Com a equipe") — migrações `atendimento.0004` e `0005` (preenche o resumo das conversas existentes). Signal do resumo, proteção em `Conversa.save()`, eco das mensagens da equipe, notas fora do histórico da IA. Indicador "Aguardando a equipe" do Início leva às conversas. 119 testes.
- **Mensagens seguidas ("oi" / "boa tarde" / "quero marcar"):** o assistente respondia só o cumprimento — nos testes reais o intervalo entre mensagens foi de 14–20 s (maior que a espera de 8 s) e a IA leva 10–60 s. Agora: +12 s de espera quando a mensagem é só cumprimento, nova checagem de "mensagem mais nova" ao pegar a trava, resposta descartada se chegou mensagem durante a geração (salvo se houve ação/transferência), regra no prompt para responder tudo junto. 165 testes.
- Roteiro da 1ª reunião com o cliente (levantamento de requisitos, demonstração, custos para a clínica) publicado como página: https://claude.ai/artifact/MB48seTed3nHa8ZXYjHqfC
- **Análise dos Logs a pedido do responsável** (testes de resposta/assumir funcionando): 3 erros "database is locked" (SQLite + rajada de webhooks) → SQLite em WAL/IMMEDIATE/timeout 20 s e webhook sem gravação quando o status não muda; 4 avisos "sem worker" (esperado: rodando sem Celery) → aviso limitado a 1 a cada 10 min; 1 aviso de modelo reserva do Gemini (funcionando como planejado). 160 testes.
- **Etapa 7 — confirmação D-1, lembrete e fila:** `atendimento/lembretes.py` (pedido de confirmação na véspera, lembrete N horas antes, janela 8h–20h, "SIM" confirma direto no webhook), configuração na página Assistente (+ botão "Enviar pendentes agora"), status dos envios na lista de consultas; Celery 5.6 + Redis (`config/celery.py`, `atendimento/tasks.py`, beat a cada 5 min, `acks_late`), assistente vai para a fila quando há worker vivo (senão thread), comando `enviar_lembretes`, `iniciar_dev.ps1`, Redis da fila no compose. Campos `Configuracao.confirmacao_*`/`lembrete_horas_antes` e `Agendamento.aviso_dia_enviado_em` (migrações `integracoes.0005`, `atendimento.0006`). Regra 10 no prompt. Worker real validado no Windows; nenhuma mensagem enviada (sem consultas futuras). 148 testes.
- **Abas da caixa de entrada não rolavam com mouse** (rolagem lateral com barra escondida): rótulos curtos (Equipe / Assistente / Encerradas / Todas, descrição completa no `title`), quebra de linha como garantia, coluna da lista até 380 px. Menu do celular ganhou `data-rolagem-lateral` (rodinha, arrastar, esmaecimento) — verificado no navegador.
- **Tipos de mensagem no webhook:** reações/figurinhas/protocolo/álbum ignorados; imagem, áudio, vídeo, documento, localização e contato viram descrição legível com legenda; regra nova no prompt sobre mídia. 124 testes.
- **Verificação de fuso horário** a pedido do responsável: configuração, banco, telas (Início, Logs, Conversas), JSON do painel, janelas de atendimento e eventos lidos do Google — todos coerentes com o horário de Brasília. Nenhuma correção necessária; risco do horário de verão documentado.
- **Mudança de prioridade (1ª reunião):** CRM de reativação com a base do Dentista Organizado. DO mapeado em modo só leitura (seção 5) — nenhum dado de paciente copiado para arquivos.
- Observado ao migrar: o número de Minas recebe mensagens de contatos reais; o assistente (desligado no momento) só respondeu a conversa de teste do responsável.

### 01/10/2026
- Levantamento de requisitos e escopo da v1.
- **Etapa 1:** projeto Django (apps `core`, `integracoes`, `atendimento`, `painel`), modelos, painel, logs no banco, cliente da Evolution, webhook, criptografia de segredos, 14 testes.
- **Etapa 2:** Docker + Evolution v2.3.7; correção de `instancia_existe` (404 da API real); WhatsApp de Minas conectado e recebendo.
- **Auditoria de segredos:** removidos defaults inseguros de `SECRET_KEY`/`WEBHOOK_TOKEN`; `DEBUG` passa a ser `False` por padrão; senha do Postgres da Evolution saiu do compose para o `.env` (e foi rotacionada); `.gitignore` robusto.
- **Redesign do front-end:** Bootstrap removido; design system próprio (`static/css/painel.css`), modo escuro, Phosphor Icons, nome da clínica dinâmico, estados vazios, toasts, tela de login nova, tema no admin, 404. Filtros `painel_tags` removidos (sem uso).
- Criados `CLAUDE.md` e `DEPLOY.md`.
- **Etapa 3 (código):** `integracoes/google.py` (OAuth com state assinado + nonce de sessão, renovação automática de token, calendarList, freeBusy, revogação); telas de conectar/desconectar/reconectar, página por unidade para escolher o calendário de cada dentista e testar horários livres; `atendimento/agenda.py` (disponibilidade); nova variável `URL_SITE`; "Verificar status" inclui o Google. 37 testes. **Pendente:** teste com credenciais reais.
- Teste real do Google: conexão OK (após adicionar o e-mail como usuário de teste). "Nenhum horário livre" era o dentista de Minas **sem horário de atendimento** — a tela agora avisa isso com link para cadastrar (+2 testes, 39 no total). `ManifestStaticFilesStorage` ligado quando `DEBUG=false`.
- **Roteiro reordenado:** agendamento passou a ser a etapa 4 e a IA a 5.
- **Etapa 5 (código):** SDK `anthropic` 1.11; `atendimento/assistente.py` (prompt em 2 blocos com cache, 8 ferramentas strict, ciclo com fallback automático e tratamento de recusa); orquestração em `services.py` (thread + espera + trava por conversa, eco do robô por texto, falha → mensagem padrão + transferência, alerta no `telefone_alerta`); webhook aciona o assistente; página **Assistente** no painel (configuração + simulador); campos novos em `Configuracao` e `Conversa.simulada` (migrações `integracoes.0002`, `atendimento.0003`); modelo padrão trocado para `claude-opus-5-5`. 83 testes.
- **1º teste pelo WhatsApp:** chegou a mensagem de falha — o Gemini respondeu 503 ("high demand"). Adicionados: 4 tentativas com espera crescente, **modelo reserva** (campo `ia_modelo_reserva`, migração `integracoes.0004`; padrão `gemini-3.5-flash`, conferido disponível na conta), mensagem clara para 503 nos Logs, ação de admin **"Devolver ao assistente"**. Conversa de teste devolvida ao assistente. 105 testes.
- **Gemini validado de verdade:** chave conectada; no simulador, "quero marcar uma limpeza" → chamou `buscar_horarios_livres` (assinatura do Gemini preservada, sem erro 400) → ofereceu horários reais e pediu o nome. Troca de número do WhatsApp de Minas também confirmada nos logs.
- **IA gratuita para testes:** pesquisa de APIs gratuitas (Gemini, Groq, OpenRouter, Mistral…); escolhido **Gemini**. Novo campo `Configuracao.ia_provedor` + `ia_base_url` (migração `integracoes.0003`); SDK `openai` 3.23; `_turno_compativel` com preservação da `thought_signature`, conversão de ferramentas para o subconjunto do Gemini, filtro de conteúdo → transferência; verificação de chave por provedor; aviso de privacidade no painel. Configuração local já em Gemini / `gemini-3.8-flash` (falta a chave). 95 testes.
- **Tela do QR code:** ícone de "conectado" aparecia antes do QR (CSS vencendo `hidden`) e o QR não vinha na 1ª vez (Evolution ainda gerando) — corrigidos com regra global `[hidden]`, novas tentativas e renovação automática a cada 40 s. 64 testes.
- **Correções do WhatsApp:** (1) "Desconectar" não funcionava — `window.confirm` suprimido pelo navegador embutido; trocado por diálogo próprio (vale também para desconectar Google e cancelar consulta). (2) Mensagens perdidas quando `pushName` vinha `null`. (3) Troca de número: número normalizado/validado, QR code gerado para o número atual, log da troca, desconectar tolerante quando o celular já saiu. 62 testes.
- **Etapa 4 (código):** `atendimento/agendamentos.py` (marcar/confirmar/cancelar/remarcar com transação banco+Google), `criar_evento`/`atualizar_evento` em `google.py`, campos novos em `Agendamento` (origem, calendário, confirmado/cancelado em, motivo — migração `0002`), remarcação que ignora o próprio horário, `normalizar_telefone` (rejeita número estrangeiro), tela com horários clicáveis e lista de próximas consultas. 55 testes.
- **Etapa 3 validada pelo responsável:** com horários cadastrados no dentista de Minas, um evento criado no Google Agenda sumiu corretamente da lista de horários livres.
- Webhook passou a logar `connection.update` só quando o status muda. Subtítulo de Integrações corrigido (a página não verifica o status ao abrir).
