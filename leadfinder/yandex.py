"""Работа с Яндексом — дополнение к 2GIS, не замена.

ВАЖНО ПРО ОГРАНИЧЕНИЕ. Официальный Яндекс «Поиск по организациям»
(search-maps.yandex.ru) отдаёт название, адрес, телефон, категорию и
часы работы, но НЕ отдаёт рейтинг и число отзывов — их в API просто нет.
Рейтинг живёт только в интерфейсе Яндекс.Карт.

Отсюда рабочая схема:
  1. фильтруем по рейтингу через 2GIS, где рейтинг в API есть;
  2. этим модулем находим ту же организацию у Яндекса и получаем ссылку
     на её карточку;
  3. рейтинг на Яндексе смотрим глазами по 20-30 отобранным компаниям
     перед звонком — это всё равно делается вручную при подготовке.

Парсить выдачу Яндекс.Карт в обход API не нужно: это против их правил
использования, а отобранных лидов всё равно проверяешь руками.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import quote_plus

import requests

from .models import Company

log = logging.getLogger(__name__)

SEARCH_URL = "https://search-maps.yandex.ru/v1/"
REQUEST_PAUSE = 0.2


def maps_search_url(company: Company) -> str:
    """Ссылка на поиск компании в Яндекс.Картах — открыть и посмотреть рейтинг."""
    parts = [company.name, company.address or company.city]
    query = " ".join(p for p in parts if p)
    return f"https://yandex.ru/maps/?text={quote_plus(query)}"


def org_card_url(org_id: str) -> str:
    return f"https://yandex.ru/maps/org/{org_id}/"


class YandexClient:
    """Ищет организацию в справочнике Яндекса, чтобы получить её org id.

    Ключ: https://developer.tech.yandex.ru (тариф «Поиск по организациям»).
    """

    def __init__(self, api_key: str, timeout: int = 15, pause: float = REQUEST_PAUSE):
        if not api_key:
            raise ValueError("Нужен ключ Яндекса: https://developer.tech.yandex.ru")
        self.api_key = api_key
        self.timeout = timeout
        self.pause = pause
        self.session = requests.Session()

    def find_org_id(self, company: Company) -> str:
        """Возвращает id организации в Яндексе или пустую строку."""
        text = " ".join(p for p in (company.name, company.address) if p)
        params = {
            "apikey": self.api_key,
            "text": text,
            "type": "biz",
            "lang": "ru_RU",
            "results": 1,
        }
        if company.lat is not None and company.lon is not None:
            params["ll"] = f"{company.lon},{company.lat}"
            params["spn"] = "0.02,0.02"

        try:
            response = self.session.get(SEARCH_URL, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            log.warning("Яндекс недоступен для «%s»: %s", company.name, exc)
            return ""

        if response.status_code != 200:
            log.warning("Яндекс вернул %s для «%s»", response.status_code, company.name)
            return ""

        try:
            features = response.json().get("features", [])
        except ValueError:
            return ""

        if not features:
            return ""

        meta = features[0].get("properties", {}).get("CompanyMetaData", {})
        return str(meta.get("id", ""))


def enrich(companies: list[Company], client: YandexClient | None = None) -> list[Company]:
    """Проставляет ссылку на Яндекс каждой компании.

    Без ключа ставит ссылку на поиск — её достаточно, чтобы открыть карточку
    в один клик. С ключом находит точный id и ставит прямую ссылку.
    """
    for company in companies:
        if client is not None:
            org_id = client.find_org_id(company)
            if org_id:
                company.yandex_org_id = org_id
                company.url_yandex = org_card_url(org_id)
                time.sleep(client.pause)
                continue
            time.sleep(client.pause)

        company.url_yandex = maps_search_url(company)

    return companies
