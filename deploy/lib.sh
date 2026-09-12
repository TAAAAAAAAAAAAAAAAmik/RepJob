#!/usr/bin/env bash
# Общие функции для установки без root. Подключается через source.
#
# Живость бота определяем по файлу блокировки и PID-файлу, а не по pgrep.
# Шаблон вида "-m bot" ловит любой процесс, у которого эта строка просто
# встречается в командной строке, — включая скрипт, который сам же и ищет.
# На таком поиске легко и отрапортовать об успехе впустую, и убить чужое.

bot_is_running() {
    local lock="$1/.lock"
    [ -e "$lock" ] || return 1
    # Лок держит работающий процесс всё время жизни. Смогли взять — бота нет.
    if ( flock -n 9 || exit 1 ) 9>"$lock" 2>/dev/null; then
        return 1
    fi
    return 0
}

bot_pid() {
    local pidfile="$1/bot.pid"
    [ -f "$pidfile" ] || return 1

    local pid
    pid=$(cat "$pidfile" 2>/dev/null) || return 1
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1

    # PID-файл мог устареть, а номер — достаться постороннему процессу.
    # Без этой проверки остановка бота убила бы чужое.
    if [ -r "/proc/$pid/cmdline" ]; then
        tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q -- '-m bot' || return 1
    fi

    echo "$pid"
}

bot_stop() {
    local app_dir="$1"
    local pid
    pid=$(bot_pid "$app_dir") || return 1

    kill "$pid" 2>/dev/null || return 1

    # Даём завершиться по-хорошему: aiogram корректно закрывает поллинг
    local i
    for i in $(seq 1 10); do
        kill -0 "$pid" 2>/dev/null || return 0
        sleep 1
    done

    kill -9 "$pid" 2>/dev/null || true
    return 0
}
