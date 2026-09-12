#!/usr/bin/env bash
# Останавливает бота, установленного без root.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$(dirname "$HERE")}"
. "$HERE/lib.sh"

if bot_stop "$APP_DIR"; then
    echo "Остановлен."
else
    echo "Бот и так не работал."
fi
