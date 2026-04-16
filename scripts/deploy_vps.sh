#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/chamados}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-chamados}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/.venv}"
PYTHON_BIN="${PYTHON_BIN:-$VENV_DIR/bin/python}"
PIP_BIN="${PIP_BIN:-$VENV_DIR/bin/pip}"
DEPLOY_USER="${DEPLOY_USER:-$(id -un)}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"
AUTO_PAUSE_CRON_FILE="${AUTO_PAUSE_CRON_FILE:-/etc/cron.d/chamados-autopause}"

echo "==> Deploy do projeto em ${PROJECT_DIR}"
cd "$PROJECT_DIR"

echo "==> Atualizando codigo (${BRANCH})"
git fetch origin "$BRANCH"
git pull --ff-only origin "$BRANCH"

echo "==> Atualizando dependencias"
"$PIP_BIN" install -r requirements.txt

echo "==> Aplicando migracoes"
"$PYTHON_BIN" manage.py migrate --noinput

echo "==> Sincronizando equipe TI padrao"
"$PYTHON_BIN" manage.py ensure_ti_members fabiano.polone fabio.generoso marcelo.sorigotti

echo "==> Validando projeto"
"$PYTHON_BIN" manage.py check

echo "==> Garantindo rotina de pausa automatica as 17:45"
mkdir -p "$LOG_DIR"
sudo tee "$AUTO_PAUSE_CRON_FILE" >/dev/null <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
CRON_TZ=America/Sao_Paulo

45 17 * * * $DEPLOY_USER cd $PROJECT_DIR && $PYTHON_BIN manage.py autopause_open_tickets >> $LOG_DIR/autopause_open_tickets.log 2>&1
EOF
sudo chmod 0644 "$AUTO_PAUSE_CRON_FILE"

echo "==> Reiniciando servico ${SERVICE_NAME}"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager

echo "==> Deploy concluido"
