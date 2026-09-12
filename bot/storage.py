"""Небольшое состояние, переживающее перезапуск бота.

Города запоминаются, чтобы повторный поиск был в один тап, а не в набор
названия с телефона. Хранится в JSON рядом с установкой — базы данных
ради десятка строк заводить незачем.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

MAX_CITIES = 6


class Storage:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("BOT_STATE_FILE", "state.json"))
        self._data: dict[str, dict] = self._load()

    # ------------------------------------------------------------------ файл

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Битый файл не повод падать: начнём с чистого листа
            log.warning("Не смог прочитать %s (%s), начинаю заново", self.path, exc)
            return {}

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

    # ------------------------------------------------------------- города

    def recent_cities(self, user_id: int) -> list[str]:
        return list(self._data.get(str(user_id), {}).get("cities", []))

    def remember_city(self, user_id: int, city: str) -> None:
        city = city.strip()
        if not city:
            return

        key = str(user_id)
        cities = self.recent_cities(user_id)

        # Уже знакомый город поднимаем наверх, а не дублируем
        cities = [c for c in cities if c.casefold() != city.casefold()]
        cities.insert(0, city)

        self._data.setdefault(key, {})["cities"] = cities[:MAX_CITIES]
        self._save()

    def forget_cities(self, user_id: int) -> None:
        self._data.setdefault(str(user_id), {})["cities"] = []
        self._save()
