"""Модель компании-лида и правила отбора."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any

# Целевой рейтинг, к которому мы ведём клиента. Используется в расчёте
# потенциала: чем дальше карточка от цели, тем больнее владельцу.
TARGET_RATING = 4.5


@dataclass
class Company:
    """Одна компания из справочника."""

    source: str                      # "2gis"
    source_id: str
    name: str
    rubric: str = ""
    address: str = ""
    city: str = ""
    phone: str = ""
    website: str = ""
    rating: float | None = None
    review_count: int = 0
    lat: float | None = None
    lon: float | None = None
    url_2gis: str = ""
    url_yandex: str = ""             # заполняется поиском по Яндексу
    yandex_org_id: str = ""

    # Считается на этапе скоринга
    score: int = 0
    verdict: str = ""
    reviews_needed: int | None = None

    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def as_row(self) -> dict[str, Any]:
        row = asdict(self)
        row.pop("extra", None)
        return row


def reviews_needed_for(rating: float, count: int, target: float = TARGET_RATING) -> int | None:
    """Сколько пятёрок нужно, чтобы поднять карточку с rating до target.

    Формула из руководства: N = count * (target - rating) / (5 - target).
    Возвращает None, если цель недостижима или уже достигнута.
    """
    if count <= 0 or target >= 5:
        return None
    if rating >= target:
        return 0
    return math.ceil(count * (target - rating) / (5 - target))


def is_target(
    company: Company,
    rating_min: float = 3.0,
    rating_max: float = 4.2,
    min_reviews: int = 10,
    require_phone: bool = False,
) -> bool:
    """Проходит ли компания под наш профиль клиента.

    Границы по умолчанию — из руководства: ниже 3.0 обычно реально плохой
    сервис и вытянуть его нельзя, выше 4.2 у владельца не болит. Отзывов
    меньше десятка — рейтинг статистический шум, его качнёт само собой.
    """
    if company.rating is None:
        return False
    if not (rating_min <= company.rating <= rating_max):
        return False
    if company.review_count < min_reviews:
        return False
    if require_phone and not company.phone:
        return False
    return True
