"""Обёртка над leadfinder для асинхронного бота.

Клиент 2GIS синхронный (requests), а поиск по нескольким рубрикам идёт
десятки секунд. Если позвать его прямо из хендлера, бот встанет колом для
всех остальных — поэтому вся работа уезжает в отдельный поток.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from dataclasses import dataclass

from leadfinder import dgis, presets, scoring, yandex
from leadfinder.export import COLUMNS
from leadfinder.models import Company, is_target

log = logging.getLogger(__name__)

RATING_MIN = float(os.environ.get("BOT_RATING_MIN", "3.0"))
RATING_MAX = float(os.environ.get("BOT_RATING_MAX", "4.2"))
MIN_REVIEWS = int(os.environ.get("BOT_MIN_REVIEWS", "10"))


@dataclass
class SearchResult:
    city: str
    queries: list[str]
    found_total: int
    companies: list[Company]


def _run_search(
    api_key: str,
    city: str,
    queries: list[str],
    max_pages: int,
    max_results: int,
    yandex_key: str = "",
) -> SearchResult:
    """Синхронная часть — выполняется в отдельном потоке."""
    client = dgis.DgisClient(api_key)
    companies = dgis.collect(client, city, queries, max_pages=max_pages)

    selected = [
        c for c in companies
        if is_target(c, rating_min=RATING_MIN, rating_max=RATING_MAX, min_reviews=MIN_REVIEWS)
    ]
    selected = scoring.rank(selected)[:max_results]
    # С ключом Яндекса подтягиваем телефоны: у 2GIS контакты платные
    client = yandex.YandexClient(yandex_key) if yandex_key else None
    yandex.enrich(selected, client)

    return SearchResult(
        city=city,
        queries=queries,
        found_total=len(companies),
        companies=selected,
    )


async def find(
    api_key: str,
    city: str,
    niche: str,
    max_pages: int = 5,
    max_results: int = 60,
    yandex_key: str = "",
) -> SearchResult:
    """Ищет лидов, не блокируя бота."""
    queries = presets.resolve(list(presets.PRESETS)) if niche == "__all__" else presets.resolve([niche])

    return await asyncio.to_thread(
        _run_search, api_key, city, queries, max_pages, max_results, yandex_key,
    )


def to_csv_bytes(companies: list[Company]) -> bytes:
    """CSV в память — чтобы отдать файлом, не трогая диск."""
    import csv

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([title for _, title in COLUMNS])

    for company in companies:
        row = company.as_row()
        writer.writerow(["" if row.get(key) is None else row.get(key) for key, _ in COLUMNS])

    # BOM, чтобы русский Excel открыл файл без мастера импорта
    return buffer.getvalue().encode("utf-8-sig")
