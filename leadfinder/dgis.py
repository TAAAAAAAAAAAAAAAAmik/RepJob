"""Клиент 2GIS Places API — основной источник рейтингов.

Официальный API, ключ берётся бесплатно на dev.2gis.ru.
Документация: https://docs.2gis.com/ru/api/search/places/overview

Важная особенность: при ошибке сервис всё равно отвечает HTTP 200,
а настоящий код лежит в meta.code — поэтому статус ответа проверять
бесполезно, разбираем тело.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterator

import requests

from .models import Company

log = logging.getLogger(__name__)

REGIONS_URL = "https://catalog.api.2gis.com/2.0/region/list"
ITEMS_URL = "https://catalog.api.2gis.com/3.0/items"

# Поля, которые просим отдать сверх базовых. Без items.reviews рейтинга не будет.
FIELDS = ",".join([
    "items.point",
    "items.address",
    "items.contact_groups",
    "items.reviews",
    "items.rubrics",
])

PAGE_SIZE = 10          # жёсткий максимум API: больше — ошибка 400
MAX_PAGES = 10          # 100 организаций на запрос; больше редко нужно, а лимит ключа не резиновый
REQUEST_PAUSE = 0.35    # пауза между страницами, чтобы не ловить лимиты


class DgisError(RuntimeError):
    """Ошибка, которую вернул сам сервис."""


class DgisClient:
    """Клиент с учётом потраченных запросов.

    Демо-ключ даёт всего 1000 запросов на месяц, поэтому счётчик здесь не
    украшение: по нему видно, во сколько обошёлся прогон.
    """

    def __init__(self, api_key: str, timeout: int = 20, pause: float = REQUEST_PAUSE):
        if not api_key:
            raise ValueError("Нужен ключ 2GIS. Получить: https://dev.2gis.ru")
        self.api_key = api_key
        self.timeout = timeout
        self.pause = pause
        self.session = requests.Session()
        self.requests_made = 0
        self._region_cache: dict[str, str] = {}

    # ------------------------------------------------------------------ http

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        params = {**params, "key": self.api_key}
        last_error: Exception | None = None

        for attempt in range(4):
            try:
                self.requests_made += 1
                response = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(2 ** attempt)
                continue

            # Сетевые лимиты и сбои — повторяем с отступом
            if response.status_code in (429, 500, 502, 503, 504):
                last_error = DgisError(f"HTTP {response.status_code}")
                time.sleep(2 ** attempt)
                continue

            payload = response.json()
            code = payload.get("meta", {}).get("code", response.status_code)

            if code == 404:
                # «Ничего не найдено» — это не ошибка, это пустой результат
                return {"result": {"items": [], "total": 0}}

            if code != 200:
                message = payload.get("meta", {}).get("error", {}).get("message", "неизвестная ошибка")
                raise DgisError(f"2GIS вернул {code}: {message}")

            return payload

        raise DgisError(f"Не удалось получить ответ от 2GIS: {last_error}")

    # --------------------------------------------------------------- regions

    def regions(self) -> list[dict[str, Any]]:
        payload = self._get(REGIONS_URL, {})
        return payload.get("result", {}).get("items", [])

    def resolve_region(self, city: str) -> str:
        """Ищет region_id по названию города. Бросает DgisError, если не нашёл.

        Результат кэшируется: список регионов меняется раз в год, а запрос
        к нему стоит столько же, сколько страница выдачи.
        """
        wanted = city.strip().casefold()
        if wanted in self._region_cache:
            return self._region_cache[wanted]

        items = self.regions()

        for item in items:
            if item.get("name", "").casefold() == wanted:
                self._region_cache[wanted] = str(item["id"])
                return self._region_cache[wanted]

        # Мягкое совпадение: «Санкт-Петербург» против «Санкт-Петербург и ЛО»
        for item in items:
            if wanted in item.get("name", "").casefold():
                self._region_cache[wanted] = str(item["id"])
                return self._region_cache[wanted]

        available = ", ".join(sorted(i.get("name", "") for i in items)[:40])
        raise DgisError(f"Город «{city}» не найден. Доступные: {available} …")

    # ---------------------------------------------------------------- search

    def search(self, query: str, region_id: str, max_pages: int = MAX_PAGES) -> Iterator[dict[str, Any]]:
        """Постранично отдаёт сырые организации по текстовому запросу."""
        for page in range(1, max_pages + 1):
            payload = self._get(ITEMS_URL, {
                "q": query,
                "region_id": region_id,
                "fields": FIELDS,
                "page": page,
                "page_size": PAGE_SIZE,
            })

            result = payload.get("result", {})
            items = result.get("items", [])
            if not items:
                return

            yield from items

            total = result.get("total", 0)
            if page * PAGE_SIZE >= total:
                return

            time.sleep(self.pause)

        log.warning("Запрос «%s»: остановился на %d страницах, в выдаче может быть больше", query, max_pages)


# ------------------------------------------------------------------ разбор


def _extract_contacts(raw: dict[str, Any]) -> tuple[str, str]:
    """Достаёт первый телефон и сайт из contact_groups."""
    phone = ""
    website = ""

    for group in raw.get("contact_groups", []) or []:
        for contact in group.get("contacts", []) or []:
            kind = contact.get("type")
            if kind == "phone" and not phone:
                phone = contact.get("value") or contact.get("text") or ""
            elif kind == "website" and not website:
                website = contact.get("url") or contact.get("text") or contact.get("value") or ""

    return phone, website


def parse_company(raw: dict[str, Any], city: str = "") -> Company:
    """Превращает сырой ответ 2GIS в нашу модель."""
    reviews = raw.get("reviews") or {}
    rubrics = raw.get("rubrics") or []
    point = raw.get("point") or {}
    phone, website = _extract_contacts(raw)

    # Филиальный рейтинг — основной; если его нет, берём общий по организации
    rating = reviews.get("general_rating")
    count = reviews.get("general_review_count")
    rating_org = reviews.get("org_rating")
    count_org = reviews.get("org_review_count") or 0

    if rating is None:
        rating = rating_org
        count = count_org

    source_id = str(raw.get("id", ""))

    return Company(
        source="2gis",
        source_id=source_id,
        name=raw.get("name") or raw.get("full_name") or "",
        rubric=(rubrics[0].get("name") if rubrics else ""),
        address=raw.get("address_name") or raw.get("full_address_name") or "",
        city=city,
        phone=phone,
        website=website,
        rating=float(rating) if rating is not None else None,
        review_count=int(count or 0),
        rating_org=float(rating_org) if rating_org is not None else None,
        review_count_org=int(count_org),
        lat=point.get("lat"),
        lon=point.get("lon"),
        url_2gis=f"https://2gis.ru/firm/{source_id.split('_')[0]}" if source_id else "",
    )


def collect(
    client: DgisClient,
    city: str,
    queries: list[str],
    max_pages: int = MAX_PAGES,
) -> list[Company]:
    """Собирает компании по списку запросов, убирая дубли между рубриками."""
    region_id = client.resolve_region(city)
    log.info("Город %s → region_id=%s", city, region_id)

    seen: set[str] = set()
    companies: list[Company] = []

    for query in queries:
        found = 0
        for raw in client.search(query, region_id, max_pages=max_pages):
            company = parse_company(raw, city=city)
            if not company.source_id or company.source_id in seen:
                continue
            seen.add(company.source_id)
            companies.append(company)
            found += 1
        log.info("  «%s»: %d организаций", query, found)

    return companies
