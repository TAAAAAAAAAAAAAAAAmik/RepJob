#!/usr/bin/env bash
# Перезапуск бота, установленного без root.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$(dirname "$HERE")}"
. "$HERE/lib.sh"

bot_stop "$APP_DIR" >/dev/null || true
sleep 1

nohup "$APP_DIR/start.sh" >/dev/null 2>&1 &
sleep 6

if bot_is_running "$APP_DIR"; then
    echo "Бот перезапущен."
else
    echo "Не поднялся. Последние строки лога:"
    tail -20 "$APP_DIR/bot.log" 2>/dev/null || echo "(лог пуст)"
    exit 1
fi
