"""Состояние, переживающее перезапуск бота: доступы и память городов.

Доступы держим здесь, а не только в переменных окружения: выдать их
и забрать нужно на ходу, из чата, без правки файла и перезапуска.

Хранится в JSON рядом с установкой — базы данных ради пары десятков
строк заводить незачем.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_CITIES = 6
VERSION = 2

# Не дёргаем админа чаще, чем раз в полчаса, из-за одного и того же человека
NOTIFY_COOLDOWN = 30 * 60


class Storage:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("BOT_STATE_FILE", "state.json"))
        self._data = self._load()

    # ------------------------------------------------------------------ файл

    def _load(self) -> dict[str, Any]:
        blank: dict[str, Any] = {"version": VERSION, "users": {}, "access": {}, "notified": {}}

        if not self.path.exists():
            return blank

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Битый файл не повод падать: начнём с чистого листа
            log.warning("Не смог прочитать %s (%s), начинаю заново", self.path, exc)
            return blank

        if not isinstance(raw, dict):
            return blank

        # Первая версия хранила города прямо под id пользователя
        if "version" not in raw:
            blank["users"] = {k: v for k, v in raw.items() if isinstance(v, dict)}
            return blank

        for key in ("users", "access", "notified"):
            raw.setdefault(key, {})
        return raw

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Пишем через временный файл: прерванная запись не оставит огрызок
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.path.parent,
                prefix=".state-", suffix=".tmp", delete=False,
            ) as handle:
                json.dump(self._data, handle, ensure_ascii=False, indent=2)
                tmp = Path(handle.name)
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("Не смог сохранить %s: %s", self.path, exc)

    # --------------------------------------------------------------- доступы

    def is_allowed(self, user_id: int) -> bool:
        return str(user_id) in self._data["access"]

    def grant(self, user_id: int, name: str = "", username: str = "", by: int | None = None) -> None:
        self._data["access"][str(user_id)] = {
            "name": name,
            "username": username,
            "granted_at": int(time.time()),
            "granted_by": by,
        }
        # Выдали доступ — прежний отказ больше не актуален
        self._data["notified"].pop(str(user_id), None)
        self._save()

    def revoke(self, user_id: int) -> bool:
        """Возвращает True, если доступ действительно был."""
        existed = self._data["access"].pop(str(user_id), None) is not None
        if existed:
            self._save()
        return existed

    def allowed_users(self) -> list[tuple[int, dict[str, Any]]]:
        """Все выданные доступы, свежие сверху."""
        items = [(int(k), v) for k, v in self._data["access"].items() if k.lstrip("-").isdigit()]
        return sorted(items, key=lambda pair: pair[1].get("granted_at", 0), reverse=True)

    # ------------------------------------------------------- заявки на доступ

    def should_notify(self, user_id: int, name: str = "", username: str = "") -> bool:
        """Пора ли сообщать админу о стуке этого человека.

        Заодно запоминает, как его зовут: в кнопку выдачи помещается только
        номер, а подписывать доступ именем всё равно нужно.
        """
        record = self._data["notified"].get(str(user_id)) or {}
        if not isinstance(record, dict):
            record = {}

        if time.time() - record.get("at", 0) < NOTIFY_COOLDOWN:
            return False

        self._data["notified"][str(user_id)] = {
            "at": int(time.time()), "name": name, "username": username,
        }
        self._save()
        return True

    def pending_info(self, user_id: int) -> dict[str, Any]:
        """Что известно о постучавшемся: имя и ник, если сохранились."""
        record = self._data["notified"].get(str(user_id))
        return record if isinstance(record, dict) else {}

    # --------------------------------------------------------------- города

    def recent_cities(self, user_id: int) -> list[str]:
        return list(self._data["users"].get(str(user_id), {}).get("cities", []))

    def remember_city(self, user_id: int, city: str) -> None:
        city = city.strip()
        if not city:
            return

        cities = self.recent_cities(user_id)
        # Уже знакомый город поднимаем наверх, а не дублируем
        cities = [c for c in cities if c.casefold() != city.casefold()]
        cities.insert(0, city)

        self._data["users"].setdefault(str(user_id), {})["cities"] = cities[:MAX_CITIES]
        self._save()

    def forget_cities(self, user_id: int) -> None:
        self._data["users"].setdefault(str(user_id), {})["cities"] = []
        self._save()
