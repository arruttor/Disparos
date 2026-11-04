# Automação de Clínica Odontológica

## Produto atual: disparos de mensagens (03/11/2026)

Painel para importar pacientes de uma planilha Excel/CSV e enviar uma mensagem inicial personalizada pelo WhatsApp, via Evolution. Sem IA ou Google Agenda. Respostas e conversas do celular não são importadas.

**Uma tentativa automática por paciente/número, permanentemente.** Falhou ou ficou incerto? Nova tentativa somente manual, após conferir o celular. Reimportar, reiniciar ou mudar mensagem/horários não remove bloqueios.

1. Instalar `requirements.txt` e executar `python manage.py migrate`.
2. Iniciar Django, Evolution e o agendador. `iniciar_dev.ps1` sobe Docker, Django, worker e beat. Como alternativa, executar `python manage.py processar_disparos` a cada minuto via Agendador de Tarefas/cron.
3. Em **Integrações**, conectar o WhatsApp da unidade. Não precisa configurar IA/Google.
4. Em **Pacientes**, importar a planilha e revisar/selecionar destinatários.
5. Em **Início**, salvar unidade, mensagem (`{nome}` ou `{primeiro_nome}`), horários e lote. Fazer teste no seu número e depois iniciar a fila.

Padrão: 09h, 14h e 17h; até 10 pacientes por lote; pelo menos 60 s entre tentativas; janela de uma hora por lote. Fila pausada inicialmente. Detalhes atuais em [AGENTS.md](AGENTS.md) e [DEPLOY.md](DEPLOY.md).

Base local desta adaptação: 1.773 pacientes de Recife; 64 para revisão de cadastro. Nenhum envio real realizado na implementação.

Testes: `python manage.py test`; verificações: `python manage.py check` e `python manage.py makemigrations --check --dry-run`.

---

## Referência histórica do atendimento anterior

O texto abaixo descreve a versão antiga. Funcionalidades de IA, Google e lembretes não participam mais da operação.

Atendimento por WhatsApp com IA + agendamento no Google Agenda + painel de gestão.

> **Vai trabalhar no projeto?** Leia primeiro o [CLAUDE.md](CLAUDE.md) (contexto, convenções, armadilhas, roteiro e histórico).
> **Vai colocar no ar?** Siga o [DEPLOY.md](DEPLOY.md).

## Estrutura

| App | Responsabilidade |
|---|---|
| `core` | Clínica, unidades, dentistas, procedimentos, horários, logs |
| `integracoes` | Configurações, Evolution API (WhatsApp), Google, IA, webhook |
| `atendimento` | Pacientes, conversas, mensagens, agendamentos |
| `painel` | Telas do superusuário (início, integrações, logs) |

Cadastros (unidades, dentistas, procedimentos, horários) ficam no admin do Django: menu **Cadastros**.

## Rodando localmente

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env          # preencha as chaves (veja comentários no arquivo)
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py dados_iniciais
.venv\Scripts\python manage.py createsuperuser
.venv\Scripts\python manage.py runserver 0.0.0.0:8000
```

Acesse http://localhost:8000.

## WhatsApp (Evolution API)

Requer Docker Desktop.

```bash
docker compose -f docker-compose.evolution.yml --env-file .env up -d
```

No painel, em **Integrações → WhatsApp**:
1. URL: `http://localhost:8080` · API key: valor de `EVOLUTION_API_KEY` do `.env`
2. **Testar conexão**
3. Na unidade, informe o número e clique em **Conectar (QR code)**

O webhook é configurado automaticamente para `URL_PUBLICA`. Com a Evolution no Docker
e o Django rodando fora dele, use `URL_PUBLICA=http://host.docker.internal:8000`.

## Testes

```bash
.venv\Scripts\python manage.py test
```

## Roteiro

- [x] 1. Base: modelos, painel, logs, cliente da Evolution, webhook
- [x] 2. Conexão real com WhatsApp (Docker + Evolution)
- [x] Auditoria de segredos + redesign do front-end
- [x] 3. OAuth do Google Agenda + horários livres
- [ ] 4. Agendamento (marcar/confirmar/remarcar/cancelar, cores de status) — código pronto
- [ ] 5. Robô com IA — código pronto, falta a chave da Anthropic
- [ ] 6. Transferência para humano
- [ ] 7. Confirmação D-1
- [ ] 8. Deploy na VPS
