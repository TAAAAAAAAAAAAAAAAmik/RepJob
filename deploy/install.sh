#!/usr/bin/env bash
#
# Установка бота на сервер. Запускать на самом сервере от root:
#
#   sudo bash deploy/install.sh
#
# Скрипт идемпотентный — можно гонять повторно для обновления.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/repjob}"
SERVICE_USER="${SERVICE_USER:-repjob}"
SERVICE_NAME="repjob-bot"
REPO_URL="${REPO_URL:-https://github.com/TAAAAAAAAAAAAAAAAmik/RepJob.git}"
BRANCH="${BRANCH:-claude/empty-repository-eskyes}"

say() { printf '\n\033[1;32m==>\033[0m %s\n' "$1"; }
die() { printf '\n\033[1;31mОшибка:\033[0m %s\n' "$1" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Нужен root: sudo bash deploy/install.sh"

# --------------------------------------------------------------- зависимости

say "Ставлю системные пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git >/dev/null

# ------------------------------------------------------------- пользователь

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    say "Создаю системного пользователя $SERVICE_USER"
    # Без shell и без домашнего каталога: этот аккаунт только для сервиса
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
else
    say "Пользователь $SERVICE_USER уже есть"
fi

# ------------------------------------------------------------------- код

if [ -d "$APP_DIR/.git" ]; then
    say "Обновляю код в $APP_DIR"
    git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
    git -C "$APP_DIR" checkout --quiet "$BRANCH"
    git -C "$APP_DIR" reset --hard --quiet "origin/$BRANCH"
else
    say "Клонирую репозиторий в $APP_DIR"
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

# --------------------------------------------------------------- окружение

say "Собираю виртуальное окружение"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# ------------------------------------------------------------------ ключи

if [ ! -f "$APP_DIR/.env" ]; then
    say "Создаю $APP_DIR/.env — заполни его перед запуском"
    cat > "$APP_DIR/.env" <<'ENVFILE'
# Токен от @BotFather
BOT_TOKEN=

# Ключ 2GIS Places API — https://dev.2gis.ru
DGIS_API_KEY=

# Кому можно пользоваться ботом. Свой номер узнаешь командой /id.
# Несколько — через запятую. Пока пусто, бот не пустит никого.
BOT_ALLOWED_IDS=

# Границы отбора — можно не трогать
BOT_RATING_MIN=3.0
BOT_RATING_MAX=4.2
BOT_MIN_REVIEWS=10
ENVFILE
    NEEDS_CONFIG=1
else
    say "Файл .env уже есть, не трогаю"
    NEEDS_CONFIG=0
fi

chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"

# ------------------------------------------------------------------ сервис

say "Ставлю systemd-юнит"
install -m 644 "$APP_DIR/deploy/$SERVICE_NAME.service" "/etc/systemd/system/$SERVICE_NAME.service"
systemctl daemon-reload
systemctl enable --quiet "$SERVICE_NAME"

if [ "$NEEDS_CONFIG" -eq 1 ]; then
    cat <<EOF

Почти всё. Осталось два шага:

  1. Заполнить ключи:   nano $APP_DIR/.env
  2. Запустить:         systemctl start $SERVICE_NAME

Потом напиши боту /id, положи свой номер в BOT_ALLOWED_IDS и перезапусти:
  systemctl restart $SERVICE_NAME

EOF
else
    say "Перезапускаю сервис"
    systemctl restart "$SERVICE_NAME"
    sleep 2
    systemctl --no-pager --lines=10 status "$SERVICE_NAME" || true
fi

cat <<EOF
Полезное:
  журнал:      journalctl -u $SERVICE_NAME -f
  статус:      systemctl status $SERVICE_NAME
  перезапуск:  systemctl restart $SERVICE_NAME
EOF
