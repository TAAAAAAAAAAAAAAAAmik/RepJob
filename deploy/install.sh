#!/usr/bin/env bash
#
# Установка бота на сервер. Запускать на самом сервере от root:
#
#   curl -fsSL https://raw.githubusercontent.com/TAAAAAAAAAAAAAAAAmik/RepJob/\
#     claude/empty-repository-eskyes/deploy/install.sh -o install.sh
#   sudo bash install.sh
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

[ "$(id -u)" -eq 0 ] || die "Нужен root: sudo bash install.sh"

command -v apt-get >/dev/null || die \
    "Скрипт рассчитан на Debian или Ubuntu (нужен apt-get).
   На другой системе поставь вручную python3, python3-venv и git,
   дальше шаги те же — смотри README."

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
    say "Создаю $APP_DIR/.env из шаблона — заполни его перед запуском"
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
else
    say "Файл .env уже есть, не трогаю"
fi

chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"

# ------------------------------------------------------------------ сервис

say "Ставлю systemd-юнит"
install -m 644 "$APP_DIR/deploy/$SERVICE_NAME.service" "/etc/systemd/system/$SERVICE_NAME.service"
systemctl daemon-reload
systemctl enable --quiet "$SERVICE_NAME"

# ------------------------------------------------------------------ запуск

# Ключи могли быть вписаны между прогонами, так что смотрим на файл,
# а не на то, создали мы его только что или нет.
get_env() { grep -E "^$1=" "$APP_DIR/.env" | head -1 | cut -d= -f2- | tr -d '"'"'"' '; }

TOKEN_SET=$([ -n "$(get_env BOT_TOKEN)" ] && echo 1 || echo 0)
KEY_SET=$([ -n "$(get_env DGIS_API_KEY)" ] && echo 1 || echo 0)

if [ "$TOKEN_SET" -eq 0 ] || [ "$KEY_SET" -eq 0 ]; then
    cat <<EOF

Почти всё. Осталось вписать ключи:

  nano $APP_DIR/.env

Нужны BOT_TOKEN (от @BotFather) и DGIS_API_KEY (platform.2gis.ru).
Потом запусти этот же скрипт ещё раз — он поднимет сервис и проверит,
что тот живой:

  sudo bash $APP_DIR/deploy/install.sh

EOF
    exit 0
fi

say "Запускаю сервис"
systemctl restart "$SERVICE_NAME"
sleep 4

if systemctl is-active --quiet "$SERVICE_NAME"; then
    # || true обязателен: без совпадения grep вернёт 1, а при pipefail
    # это убило бы скрипт ровно в момент успешного запуска
    BOT_NAME=$(journalctl -u "$SERVICE_NAME" --no-pager --lines=40 \
        | grep -oP 'Запущен как \K@\S+' | tail -1 || true)

    say "Бот работает${BOT_NAME:+: $BOT_NAME}"

    if [ -z "$(get_env BOT_ALLOWED_IDS)" ]; then
        cat <<EOF

Последний шаг: бот пока никого не пускает.

  1. Напиши ему команду /id — он ответит твоим номером
  2. Впиши номер:  nano $APP_DIR/.env   →   BOT_ALLOWED_IDS=твой_номер
  3. Перезапусти:  systemctl restart $SERVICE_NAME

EOF
    else
        echo
        echo "Всё готово. Пиши боту /find"
        echo
    fi
else
    printf '\n\033[1;31mСервис не поднялся.\033[0m Последние строки журнала:\n\n'
    journalctl -u "$SERVICE_NAME" --no-pager --lines=25 || true
    echo
    die "Разберись по журналу выше и запусти скрипт заново."
fi

cat <<EOF
Полезное:
  журнал:      journalctl -u $SERVICE_NAME -f
  статус:      systemctl status $SERVICE_NAME
  перезапуск:  systemctl restart $SERVICE_NAME
  обновление:  sudo bash $APP_DIR/deploy/install.sh
EOF
