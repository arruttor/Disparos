# Sobe o ambiente de desenvolvimento completo, cada parte numa janela própria:
#   Docker (Evolution + Redis da fila) · Django · worker do Celery · beat do Celery
# Uso (na pasta do projeto):  powershell -ExecutionPolicy Bypass -File .\iniciar_dev.ps1
# Para parar: feche as janelas (ou Ctrl+C em cada uma). O Docker continua rodando.

$ErrorActionPreference = "Stop"
$raiz = $PSScriptRoot
$python = Join-Path $raiz ".venv\Scripts\python.exe"
$celery = Join-Path $raiz ".venv\Scripts\celery.exe"

# Docker Desktop instalado por usuário pode não estar no PATH.
$dockerBin = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin"
if (Test-Path $dockerBin) { $env:Path = "$dockerBin;$env:Path" }

Write-Host "1/4 Containers (Evolution + Redis da fila)..." -ForegroundColor Cyan
docker compose -f (Join-Path $raiz "docker-compose.evolution.yml") --env-file (Join-Path $raiz ".env") up -d
if ($LASTEXITCODE -ne 0) { Write-Host "Docker não respondeu. Abra o Docker Desktop e rode de novo." -ForegroundColor Red; exit 1 }

function Abrir-Janela($titulo, $comando) {
    Start-Process powershell -WindowStyle Hidden -WorkingDirectory $raiz -ArgumentList @(
        "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle = '$titulo'; & $comando"
    )
}

Write-Host "2/4 Django (http://localhost:8000)..." -ForegroundColor Cyan
Abrir-Janela "Clínica — Django" "'$python' manage.py runserver 0.0.0.0:8000"

Write-Host "3/4 Worker do Celery (fila de disparos)..." -ForegroundColor Cyan
Abrir-Janela "Clínica — Celery worker" "'$celery' -A config worker -P threads -c 4 -l info"

Write-Host "4/4 Beat do Celery (confere a fila a cada minuto)..." -ForegroundColor Cyan
Abrir-Janela "Clínica — Celery beat" "'$celery' -A config beat -l info --schedule '$raiz\celerybeat-schedule'"

Write-Host "`nPronto. Painel: http://localhost:8000" -ForegroundColor Green
