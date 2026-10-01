# Deploy em Produção — Automação de Clínica Odontológica

**Documento para:** quem for colocar o sistema no ar e quem for mantê-lo
**Última atualização:** 01/10/2026
**Situação:** ⚠️ **nenhum deploy foi feito ainda.** Este documento descreve o **caminho planejado** e lista, na [seção 8](#8-o-que-falta-no-código-antes-do-primeiro-deploy), o que ainda precisa ser criado no código antes do primeiro deploy. Atualize-o a cada sessão.

---

## ⚠️ Leia primeiro

1. **A Evolution API nunca pode ficar exposta na internet.** A porta `8080` dá controle total sobre os WhatsApps conectados a quem tiver a API key. Em produção ela fica só na rede interna do Docker; o painel do Django é que fala com ela.
2. **Guarde a `CHAVE_CRIPTOGRAFIA` fora do servidor** (gerenciador de senhas). Sem ela, as chaves de API salvas pelo painel (Evolution, Google, IA) **não podem ser lidas** — teria que recadastrar tudo e reconectar o Google.
3. **O sistema se recusa a subir sem os segredos** (`DJANGO_SECRET_KEY`, `CHAVE_CRIPTOGRAFIA`, `WEBHOOK_TOKEN`) e, com `DJANGO_DEBUG=false`, também recusa os valores de exemplo `troque-...`. Isso é proposital.
4. **O banco tem dados de pacientes (LGPD):** nome, telefone, mensagens. Backup criptografado, acesso restrito, nada de copiar o banco de produção para máquina pessoal.
5. **Não mude `EVOLUTION_DB_PASSWORD` depois do primeiro `up`** sem também alterar a senha dentro do Postgres — ver [seção 9](#9-em-caso-de-problema).

---

## 1. Arquitetura de produção (planejada)

```
Internet ──HTTPS──► nginx (TLS Let's Encrypt)
                      │
                      └──► Django + gunicorn ──► PostgreSQL (dados do sistema)
                              ▲      │
              webhook (rede   │      └──► Redis + Celery (etapa 7: lembretes D-1)
              interna Docker) │
                      Evolution API ──► PostgreSQL + Redis próprios
                      (sem porta pública)
```

- Tudo em **Docker Compose** numa **VPS KVM da Hostinger** (a hospedagem compartilhada não roda Docker).
- Sugestão inicial de VPS: 2 vCPU / 8 GB RAM (plano KVM 2). A Evolution + 2 Postgres + Django cabem com folga para o volume da clínica.
- Custo da VPS e da IA serão repassados ao cliente.

---

## 2. Requisitos do servidor

| Item | Valor |
|---|---|
| SO | Ubuntu 24.04 LTS |
| Docker | Docker Engine + plugin Compose v2 |
| Domínio | Um subdomínio apontando para o IP da VPS (ex.: `painel.clinicaexemplo.com.br`) |
| Portas abertas | **22** (SSH, de preferência só com chave), **80** e **443**. Nada mais. |
| Firewall | `ufw` ativo |

```bash
sudo ufw allow OpenSSH
```

```bash
sudo ufw allow 80,443/tcp
```

```bash
sudo ufw enable
```

> ⚠️ O Docker publica portas **por fora** do `ufw` (mexe direto no iptables). Por isso a Evolution **não pode ter `ports:`** no compose de produção — não basta bloquear no firewall.

---

## 3. Primeiro deploy (procedimento planejado)

> Os passos que dependem de arquivos ainda inexistentes estão marcados com 🚧 — ver [seção 8](#8-o-que-falta-no-código-antes-do-primeiro-deploy).

### 3.1 Código

```bash
git clone <url-do-repositorio> /opt/clinica
```

```bash
cd /opt/clinica
```

### 3.2 `.env` de produção

```bash
cp .env.example .env
```

Preencha conforme a [seção 4](#4-configuração-do-env). Permissão restrita:

```bash
chmod 600 .env
```

### 3.3 Subir os serviços 🚧

```bash
docker compose -f docker-compose.prod.yml --env-file .env up -d --build
```

### 3.4 Banco, estáticos e superusuário 🚧

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py migrate
```

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py collectstatic --noinput
```

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py createsuperuser
```

> **Não** rode `dados_iniciais` em produção com dados do cliente: ele cria uma clínica de exemplo. Cadastre a clínica real, unidades, dentistas e procedimentos pelo painel → **Cadastros**.

### 3.5 HTTPS

Certificado com Certbot (ou o container de proxy escolhido). Só depois de o HTTPS funcionar, ligue as flags de segurança da [seção 4](#4-configuração-do-env).

### 3.6 Configurar as integrações pelo painel

1. **Integrações → WhatsApp:** URL `http://evolution:8080` (nome do serviço na rede Docker) e a `EVOLUTION_API_KEY`. Testar conexão.
2. Conectar o número de cada unidade pelo QR code — **com o celular da clínica em mãos**.
3. **Google Agenda:** adicionar a URL de callback de produção no Google Cloud Console antes de conectar.
4. **IA:** chave da Anthropic (conta do cliente ou repassada).

---

## 4. Configuração do `.env`

### Obrigatórios

```env
DJANGO_SECRET_KEY=<gerada>
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=painel.clinicaexemplo.com.br
DJANGO_CSRF_TRUSTED_ORIGINS=https://painel.clinicaexemplo.com.br
CHAVE_CRIPTOGRAFIA=<gerada — GUARDAR FORA DO SERVIDOR>
URL_PUBLICA=http://web:8000
WEBHOOK_TOKEN=<gerado>
EVOLUTION_API_KEY=<gerada>
EVOLUTION_DB_PASSWORD=<gerada, só letras e números>
DATABASE_NAME=clinica
DATABASE_USER=clinica
DATABASE_PASSWORD=<gerada>
DATABASE_HOST=db
DATABASE_PORT=5432
```

> **`URL_PUBLICA`** é o endereço que a **Evolution** usa para chamar o webhook. Como as duas estão na mesma rede Docker, use o nome do serviço (`http://web:8000`), não o domínio público. *Quando o OAuth do Google existir (etapa 3), o callback precisará da URL pública — provavelmente uma variável separada; atualizar aqui.*

> **`DJANGO_ALLOWED_HOSTS`** precisa conter o domínio **e** o nome do serviço usado pela Evolution (`web`), senão o webhook recebe 400. Ex.: `painel.clinicaexemplo.com.br,web`.

### Como gerar os valores

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```bash
python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(32)))"
```

(o primeiro serve para `DJANGO_SECRET_KEY` e `WEBHOOK_TOKEN`; o segundo para `CHAVE_CRIPTOGRAFIA`; o terceiro para senhas de banco e `EVOLUTION_API_KEY`)

### Segurança HTTPS 🚧

Ainda **não existe** no `settings.py`. Ao criar, ligar somente depois do HTTPS confirmado:
`SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_PROXY_SSL_HEADER` e HSTS — começar com `3600` e só depois subir para `31536000`.

---

## 5. Deploys seguintes (atualização)

> Faça **backup antes** ([seção 7](#7-backup)).

```bash
cd /opt/clinica
```

```bash
git pull
```

```bash
docker compose -f docker-compose.prod.yml --env-file .env up -d --build
```

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py migrate
```

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py collectstatic --noinput
```

**Atualizar a Evolution** é uma operação separada e arriscada (pode exigir reconectar os WhatsApps). Só troque a tag da imagem depois de testar a nova versão em desenvolvimento. Versão atual fixada: **v2.3.7**.

---

## 6. Verificação pós-deploy

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py check --deploy
```

Erros precisam ser resolvidos. Avisos de HSTS/SSL são esperados até a seção 4 (HTTPS) ser concluída.

Teste funcional:

1. Login no painel funciona; `/admin/` (Cadastros) abre com o tema do painel.
2. **Integrações → Verificar status:** WhatsApp de cada unidade **Conectado**; IA **Conectado**.
3. Mandar uma mensagem de **outro celular** para o número da unidade → aparece em **Cadastros → Conversas** e o contador do Início sobe.
4. Responder pelo celular da clínica → a conversa fica com o robô pausado (campo *robô pausado até*).
5. **Logs:** sem erros novos.
6. De fora do servidor, `http://IP-DA-VPS:8080` **não** pode responder.

---

## 7. Backup

| O quê | Como | Frequência |
|---|---|---|
| Banco do sistema | `pg_dump` do serviço `db` → arquivo criptografado fora da VPS | diário |
| Banco + volume da Evolution | `pg_dump` do `evolution-db` + volume `evolution_instances` | diário (sem isso, os WhatsApps precisam ser reconectados após uma perda) |
| `.env` | cópia offline no gerenciador de senhas | a cada mudança |

```bash
docker compose -f docker-compose.prod.yml exec -T db pg_dump -U clinica clinica | gzip > backup-clinica-$(date +%Y%m%d-%H%M).sql.gz
```

Confirme que o arquivo foi gerado e não está vazio. Teste uma restauração ao menos uma vez antes de precisar dela.

---

## 8. O que falta no código antes do primeiro deploy

| # | Pendência | Risco se ignorado |
|---|---|---|
| 1 | **`Dockerfile`** do Django e **`docker-compose.prod.yml`** (web, db, nginx/proxy, evolution sem `ports:`, evolution-db, evolution-redis; depois redis + celery) | Não há como subir |
| 2 | Adicionar **`gunicorn`** e **`psycopg[binary]`** ao `requirements.txt` | Django não roda em produção / não conecta no Postgres |
| 3 | Bloco de **segurança HTTPS** no `settings.py`, controlado por variável do `.env` | Cookies de sessão sem flag de segurança |
| 4 | Mover o **token do webhook** da query string para um **header** (a Evolution permite headers no webhook) | Token aparece em logs de acesso |
| 5 | Servir **estáticos** (nginx ou WhiteNoise) | CSS/JS não carregam com `DEBUG=false` |
| 6 | Variável separada para a **URL pública** (callback do Google) vs. URL interna (webhook) | OAuth do Google não funciona atrás do proxy |
| 7 | **Retenção do `RegistroLog`** (apagar INFO antigos) — a tabela guarda telefones de pacientes | Crescimento indefinido + dado pessoal retido sem prazo (LGPD) |
| 8 | Página **500** própria (já existe a 404) | Erro em produção mostra a página padrão do servidor |
| 9 | Fontes e ícones vêm de CDN (Google Fonts, unpkg). Avaliar servir localmente | Se o CDN cair, o painel perde fonte/ícones (continua funcionando) |

---

## 9. Em caso de problema

**A aplicação não sobe:**

1. `ImproperlyConfigured: Variável X não definida` → falta no `.env`.
2. `... ainda está com o valor de exemplo` → trocar o `troque-...` por um valor gerado.
3. Todas as requisições dão 400 → `DJANGO_ALLOWED_HOSTS` sem o domínio.
4. Veja **Logs** no painel ou `docker compose logs web`.

**O WhatsApp não recebe mensagens:**

1. **Integrações → Verificar status.** Se *Desconectado*, reconectar pelo QR code.
2. O webhook chega? `docker compose logs web` deve mostrar `POST /integracoes/webhook/evolution/`. Se não: `URL_PUBLICA` errada ou `web` fora do `DJANGO_ALLOWED_HOSTS`.
3. Webhook com **403** → `WEBHOOK_TOKEN` mudou depois de a instância ser criada. Clique em **Conectar** de novo na unidade (reconfigura o webhook) ou ajuste o token.

**A Evolution não sobe depois de mexer no `.env`:**

A senha do Postgres da Evolution só é aplicada na primeira inicialização do volume. Para trocar com o volume existente:

```bash
docker compose -f docker-compose.prod.yml exec evolution-db psql -U evolution -d evolution -c "ALTER USER evolution WITH PASSWORD 'NOVA_SENHA';"
```

Depois atualize `EVOLUTION_DB_PASSWORD` no `.env` e rode `up -d`.

**As chaves do painel aparecem vazias depois de um deploy:**

A `CHAVE_CRIPTOGRAFIA` mudou. Restaure a original do backup do `.env`. Não existe recuperação sem ela.

**Rollback:**

```bash
git checkout <commit-anterior>
```

Suba de novo (seção 5) e, se houve migration, restaure o backup do banco — mais seguro do que reverter migrations.

---

## 10. Referências

| Arquivo | Conteúdo |
|---|---|
| `CLAUDE.md` | Contexto do projeto, convenções, armadilhas, roteiro e histórico |
| `README.md` | Como rodar localmente |
| `.env.example` | Todas as variáveis, sem valores reais |
| `docker-compose.evolution.yml` | Evolution para **desenvolvimento** (publica a porta 8080 — não usar em produção) |
