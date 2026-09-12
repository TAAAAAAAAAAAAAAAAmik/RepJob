"""Работа с Яндексом — источник телефонов и ссылок на карточки.

Два справочника дополняют друг друга ровно там, где другой пасует:

  2GIS   отдаёт рейтинг и число отзывов, но контакты у него платные —
         на демо-ключе поле contact_groups просто не приходит в ответе;
  Яндекс отдаёт название, адрес, телефон и часы работы, но рейтинга
         и числа отзывов в его API нет вообще.

Отсюда схема: отбираем по рейтингу через 2GIS, телефоны к отобранным
подтягиваем у Яндекса. Обе части официальные, ничего парсить не нужно.

Ключ «Поиска по организациям»: https://developer.tech.yandex.ru
Без ключа модуль просто проставляет ссылки на поиск — тоже рабочий
вариант, только номер придётся открывать руками.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import quote_plus

import requests

from .models import Company

log = logging.getLogger(__name__)

SEARCH_URL = "https://search-maps.yandex.ru/v1/"
REQUEST_PAUSE = 0.2


@dataclass
class YandexOrg:
    org_id: str = ""
    name: str = ""
    phone: str = ""
    address: str = ""
    url: str = ""


def maps_search_url(company: Company) -> str:
    """Ссылка на поиск компании в Яндекс.Картах — открыть и посмотреть."""
    parts = [company.name, company.address or company.city]
    query = " ".join(p for p in parts if p)
    return f"https://yandex.ru/maps/?text={quote_plus(query)}"


def org_card_url(org_id: str) -> str:
    return f"https://yandex.ru/maps/org/{org_id}/"


# ------------------------------------------------------------------ сверка


_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize(name: str) -> set[str]:
    """Слова названия без пунктуации и регистра — для грубого сравнения."""
    cleaned = _PUNCT.sub(" ", name.casefold())
    return {w for w in cleaned.split() if len(w) > 2}


def looks_like_same(a: str, b: str) -> bool:
    """Достаточно ли похожи названия, чтобы считать это одной организацией.

    Без этой проверки Яндекс на неудачный запрос вернёт ближайшего соседа,
    и к лиду прицепится чужой телефон. Пустой номер лучше неверного:
    по чужому позвонят и попадут не туда.
    """
    words_a, words_b = _normalize(a), _normalize(b)
    if not words_a or not words_b:
        return False
    return bool(words_a & words_b)


# ------------------------------------------------------------------ клиент


class YandexClient:
    """Ищет организацию в справочнике Яндекса ради телефона и ссылки."""

    def __init__(self, api_key: str, timeout: int = 15, pause: float = REQUEST_PAUSE):
        if not api_key:
            raise ValueError("Нужен ключ Яндекса: https://developer.tech.yandex.ru")
        self.api_key = api_key
        self.timeout = timeout
        self.pause = pause
        self.session = requests.Session()
        self.requests_made = 0

    def find(self, company: Company) -> YandexOrg | None:
        """Возвращает данные организации или None, если не нашлась."""
        text = " ".join(p for p in (company.name, company.address) if p)
        if not text.strip():
            return None

        params = {
            "apikey": self.api_key,
            "text": text,
            "type": "biz",
            "lang": "ru_RU",
            "results": 1,
        }
        # Координаты из 2GIS сильно повышают шанс попасть в нужный филиал
        if company.lat is not None and company.lon is not None:
            params["ll"] = f"{company.lon},{company.lat}"
            params["spn"] = "0.01,0.01"

        try:
            self.requests_made += 1
            response = self.session.get(SEARCH_URL, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            log.warning("Яндекс недоступен для «%s»: %s", company.name, exc)
            return None

        if response.status_code == 403:
            log.warning("Яндекс отклонил ключ (403) — проверь тариф и лимиты")
            return None
        if response.status_code != 200:
            log.warning("Яндекс вернул %s для «%s»", response.status_code, company.name)
            return None

        try:
            features = response.json().get("features", [])
        except ValueError:
            return None
        if not features:
            return None

        meta = features[0].get("properties", {}).get("CompanyMetaData", {})
        found_name = meta.get("name", "")

        if not looks_like_same(company.name, found_name):
            log.info("Яндекс нашёл «%s» вместо «%s» — пропускаю", found_name, company.name)
            return None

        phones = meta.get("Phones") or []
        phone = ""
        for item in phones:
            phone = item.get("formatted") or item.get("number") or ""
            if phone:
                break

        org_id = str(meta.get("id", ""))
        return YandexOrg(
            org_id=org_id,
            name=found_name,
            phone=phone,
            address=meta.get("address", ""),
            url=org_card_url(org_id) if org_id else "",
        )


# ------------------------------------------------------------- обогащение


def enrich(companies: list[Company], client: YandexClient | None = None) -> list[Company]:
    """Проставляет ссылку на Яндекс, а с ключом — ещё и телефон.

    Без ключа ставится ссылка на поиск: её достаточно, чтобы открыть
    карточку в один клик и увидеть номер глазами.
    """
    for company in companies:
        if client is None:
            company.url_yandex = maps_search_url(company)
            continue

        org = client.find(company)
        time.sleep(client.pause)

        if org is None:
            company.url_yandex = maps_search_url(company)
            continue

        company.yandex_org_id = org.org_id
        company.url_yandex = org.url or maps_search_url(company)

        if org.phone and not company.phone:
            company.phone = org.phone
            company.phone_source = "yandex"

    return companies
