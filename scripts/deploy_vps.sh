#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/chamados}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-chamados}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/.venv}"
PYTHON_BIN="${PYTHON_BIN:-$VENV_DIR/bin/python}"
PIP_BIN="${PIP_BIN:-$VENV_DIR/bin/pip}"

echo "==> Deploy do projeto em ${PROJECT_DIR}"
cd "$PROJECT_DIR"

echo "==> Atualizando codigo (${BRANCH})"
git fetch origin "$BRANCH"
git pull --ff-only origin "$BRANCH"

echo "==> Atualizando dependencias"
"$PIP_BIN" install -r requirements.txt

echo "==> Aplicando migracoes"
"$PYTHON_BIN" manage.py migrate --noinput

echo "==> Validando projeto"
"$PYTHON_BIN" manage.py check

echo "==> Reiniciando servico ${SERVICE_NAME}"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager

echo "==> Deploy concluido"
