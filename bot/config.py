"""Настройки бота — всё из окружения, ничего в коде."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    bot_token: str
    dgis_api_key: str
    yandex_api_key: str = ""
    allowed_ids: set[int] = field(default_factory=set)

    # Ограничители, чтобы один человек не выел всю квоту 2GIS
    max_pages: int = 6
    max_results: int = 60

    @property
    def access_configured(self) -> bool:
        return bool(self.allowed_ids)

    def is_allowed(self, user_id: int) -> bool:
        return user_id in self.allowed_ids


def _parse_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.lstrip("-").isdigit():
            ids.add(int(chunk))
    return ids


def load() -> Config:
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "Нет BOT_TOKEN. Создай бота у @BotFather и положи токен в окружение:\n"
            "  export BOT_TOKEN=123456:AA..."
        )

    dgis_key = os.environ.get("DGIS_API_KEY", "").strip()
    if not dgis_key:
        raise SystemExit(
            "Нет DGIS_API_KEY. Бесплатный ключ: https://dev.2gis.ru\n"
            "  export DGIS_API_KEY=твой_ключ"
        )

    return Config(
        bot_token=token,
        dgis_api_key=dgis_key,
        yandex_api_key=os.environ.get("YANDEX_API_KEY", "").strip(),
        allowed_ids=_parse_ids(os.environ.get("BOT_ALLOWED_IDS", "")),
        max_pages=int(os.environ.get("BOT_MAX_PAGES", "6")),
        max_results=int(os.environ.get("BOT_MAX_RESULTS", "60")),
    )
