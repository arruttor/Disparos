# Automação de Clínica Odontológica

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
