#!/usr/bin/env bash
#
# Установка бота БЕЗ прав root — когда хостер не дал sudo.
#
#   curl -fsSL https://raw.githubusercontent.com/TAAAAAAAAAAAAAAAAmik/RepJob/\
#     claude/empty-repository-eskyes/deploy/install-user.sh -o install-user.sh
#   bash install-user.sh
#
# Всё живёт в ~/repjob, автозапуск через cron. Скрипт идемпотентный —
# повторный прогон обновляет код и перезапускает бота.

set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/repjob}"
BRANCH="${BRANCH:-claude/empty-repository-eskyes}"
REPO="${REPO:-TAAAAAAAAAAAAAAAAmik/RepJob}"
TARBALL="https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH"

say() { printf '\n\033[1;32m==>\033[0m %s\n' "$1"; }
die() { printf '\n\033[1;31mОшибка:\033[0m %s\n' "$1" >&2; exit 1; }

command -v python3 >/dev/null || die "На сервере нет python3, и без root его не поставить."
command -v curl    >/dev/null || die "Нужен curl."
command -v tar     >/dev/null || die "Нужен tar."

# ------------------------------------------------------------------- код

say "Скачиваю код в $APP_DIR"
mkdir -p "$APP_DIR"
# .env не лежит в репозитории, так что распаковка поверх его не затрёт
curl -fsSL "$TARBALL" | tar -xz -C "$APP_DIR" --strip-components=1

# ------------------------------------------------------------- зависимости
#
# На урезанных образах Ubuntu вырезают и venv, и pip, а доставить их
# пакетным менеджером без root нельзя. Поэтому идём по цепочке: штатный
# venv → поднимаем pip в домашний каталог → virtualenv → как есть.

PY=""
REQ="$APP_DIR/requirements.txt"
LOG="$APP_DIR/install.log"

# Каждый шаг пишется в лог целиком: когда цепочка обрывается, причина
# должна остаться на диске, а не пропасть в /dev/null.
: > "$LOG"
{
    echo "=== $(date) ==="
    echo "python: $(python3 -V 2>&1)  путь: $(command -v python3)"
    echo "система: $(uname -sm)"
} >> "$LOG"

log_run() {
    echo "--- \$ $*" >> "$LOG"
    "$@" >> "$LOG" 2>&1
}

have_pip() { python3 -m pip --version >/dev/null 2>&1; }

# На свежих системах действует PEP 668: установка в домашний каталог
# требует явного флага. Он трогает только ~/.local, систему не ломает.
pip_user() {
    log_run python3 -m pip install --user "$@" \
    || log_run python3 -m pip install --user --break-system-packages "$@"
}

setup_python() {
    # 1. Штатный venv — лучший вариант, изолирован и ничего не трогает
    if log_run python3 -m venv "$APP_DIR/.venv" \
       && "$APP_DIR/.venv/bin/python" -m pip --version >/dev/null 2>&1; then
        say "Собираю виртуальное окружение"
        PY="$APP_DIR/.venv/bin/python"
        log_run "$PY" -m pip install --upgrade pip || true
        log_run "$PY" -m pip install -r "$REQ" && return 0
    fi
    rm -rf "$APP_DIR/.venv"

    # 2. venv нет — сначала добываем pip
    if ! have_pip; then
        say "Ни venv, ни pip в системе нет — поднимаю pip в домашний каталог"
        log_run python3 -m ensurepip --upgrade --user || true

        if ! have_pip; then
            say "Скачиваю установщик pip"
            if log_run curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$APP_DIR/.get-pip.py"; then
                # Без --break-system-packages get-pip упирается в PEP 668
                log_run python3 "$APP_DIR/.get-pip.py" --user \
                || log_run python3 "$APP_DIR/.get-pip.py" --user --break-system-packages \
                || true
            fi
            rm -f "$APP_DIR/.get-pip.py"
        fi
    fi

    have_pip || return 1
    say "pip поднят: $(python3 -m pip --version 2>&1 | head -1)"

    # 3. С pip на руках собираем изолированное окружение через virtualenv:
    #    он не требует системного модуля venv и обходит запрет PEP 668
    if pip_user virtualenv \
       && log_run python3 -m virtualenv "$APP_DIR/.venv" \
       && log_run "$APP_DIR/.venv/bin/python" -m pip install -r "$REQ"; then
        say "Собрал окружение через virtualenv"
        PY="$APP_DIR/.venv/bin/python"
        return 0
    fi
    rm -rf "$APP_DIR/.venv"

    # 4. Последний вариант: пакеты прямо в ~/.local, запуск системным python
    say "Ставлю зависимости в домашний каталог"
    pip_user -r "$REQ" || return 1
    PY="python3"
    return 0
}

if ! setup_python; then
    printf '\n\033[1;31mНе удалось поставить зависимости.\033[0m Последнее из лога:\n\n'
    tail -30 "$LOG"
    printf '\nПолный лог: %s\n' "$LOG"
    exit 1
fi

# ------------------------------------------------------------------ ключи

if [ ! -f "$APP_DIR/.env" ]; then
    say "Создаю $APP_DIR/.env из шаблона"
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
fi
chmod 600 "$APP_DIR/.env"

# ------------------------------------------------------------ стартовый скрипт

say "Готовлю запуск"
cat > "$APP_DIR/start.sh" <<STARTER
#!/usr/bin/env bash
# Поднимает бота, если тот ещё не работает. Дёргается из cron.
cd "$APP_DIR" || exit 1

# Блокировка держится всё время жизни процесса: повторный запуск из cron
# просто не получит её и тихо выйдет, а после падения — получит и поднимет.
exec 9>"$APP_DIR/.lock"
flock -n 9 || exit 0

# Лог не даём разрастаться: без root logrotate не настроить
if [ -f "$APP_DIR/bot.log" ] && [ "\$(stat -c%s "$APP_DIR/bot.log")" -gt 5242880 ]; then
    tail -c 1048576 "$APP_DIR/bot.log" > "$APP_DIR/bot.log.tmp"
    mv "$APP_DIR/bot.log.tmp" "$APP_DIR/bot.log"
fi

set -a
. "$APP_DIR/.env"
set +a

# exec заменяет этот шелл питоном, PID остаётся тем же — так что
# в файле окажется настоящий номер процесса бота
echo \$\$ > "$APP_DIR/bot.pid"
exec $PY -m bot >> "$APP_DIR/bot.log" 2>&1
STARTER
chmod +x "$APP_DIR/start.sh"

# ---------------------------------------------------------------- автозапуск

if command -v crontab >/dev/null 2>&1; then
    say "Ставлю автозапуск через cron"
    # Свои строки узнаём по метке и переписываем, чужие не трогаем
    (crontab -l 2>/dev/null | grep -v '# repjob-bot' || true; \
     echo "@reboot $APP_DIR/start.sh # repjob-bot"; \
     echo "*/5 * * * * $APP_DIR/start.sh # repjob-bot") | crontab -
else
    say "crontab недоступен — автозапуск не настроен, бот будет работать до перезагрузки"
fi

# ------------------------------------------------------------------ запуск

get_env() { grep -E "^$1=" "$APP_DIR/.env" | head -1 | cut -d= -f2- | tr -d '"'"'"' '; }

if [ -z "$(get_env BOT_TOKEN)" ] || [ -z "$(get_env DGIS_API_KEY)" ]; then
    cat <<EOF

Почти всё. Осталось вписать ключи:

  nano $APP_DIR/.env

Нужны BOT_TOKEN (от @BotFather) и DGIS_API_KEY (platform.2gis.ru).
Потом запусти этот скрипт ещё раз — он поднимет бота и проверит, что тот жив:

  bash $APP_DIR/deploy/install-user.sh

EOF
    exit 0
fi

say "Запускаю бота"
. "$APP_DIR/deploy/lib.sh"

bot_stop "$APP_DIR" >/dev/null || true
sleep 1
nohup "$APP_DIR/start.sh" >/dev/null 2>&1 &
sleep 6

if bot_is_running "$APP_DIR"; then
    BOT_NAME=$(grep -oP 'Запущен как \K@\S+' "$APP_DIR/bot.log" 2>/dev/null | tail -1 || true)
    say "Бот работает${BOT_NAME:+: $BOT_NAME}"

    if [ -z "$(get_env BOT_ALLOWED_IDS)" ]; then
        cat <<EOF

Последний шаг: бот пока никого не пускает.

  1. Напиши ему /id — он ответит твоим номером
  2. Впиши номер:  nano $APP_DIR/.env   →   BOT_ALLOWED_IDS=твой_номер
  3. Перезапусти:  $APP_DIR/deploy/restart.sh

EOF
    else
        echo
        echo "Всё готово. Пиши боту /find"
        echo
    fi
else
    printf '\n\033[1;31mБот не поднялся.\033[0m Последние строки лога:\n\n'
    tail -25 "$APP_DIR/bot.log" 2>/dev/null || echo "(лог пуст)"
    echo
    die "Разберись по логу выше и запусти скрипт заново."
fi

cat <<EOF
Полезное:
  лог:         tail -f $APP_DIR/bot.log
  перезапуск:  $APP_DIR/deploy/restart.sh
  остановить:  $APP_DIR/deploy/stop.sh
  обновление:  bash $APP_DIR/deploy/install-user.sh
EOF
