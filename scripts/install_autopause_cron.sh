#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/chamados}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/.venv}"
PYTHON_BIN="${PYTHON_BIN:-$VENV_DIR/bin/python}"
DEPLOY_USER="${DEPLOY_USER:-ti}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"
AUTO_PAUSE_CRON_FILE="${AUTO_PAUSE_CRON_FILE:-/etc/cron.d/chamados-autopause}"

mkdir -p "$LOG_DIR"

sudo tee "$AUTO_PAUSE_CRON_FILE" >/dev/null <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
CRON_TZ=America/Sao_Paulo

# Pausa automatica dos chamados em atendimento as 17:45.
45 17 * * * $DEPLOY_USER cd $PROJECT_DIR && $PYTHON_BIN manage.py autopause_open_tickets >> $LOG_DIR/autopause_open_tickets.log 2>&1
EOF

sudo chmod 0644 "$AUTO_PAUSE_CRON_FILE"
sudo systemctl restart cron

echo "Cron de pausa automatica instalado em $AUTO_PAUSE_CRON_FILE"
cat "$AUTO_PAUSE_CRON_FILE"
