"""Приоритет лида: кому звонить первым."""

from __future__ import annotations

import math

from .models import Company, TARGET_RATING, reviews_needed_for

# Отзывов, при которых поток клиентов считаем заведомо достаточным.
# Дальше объём перестаёт добавлять привлекательности.
VOLUME_SATURATION = 300

# Разрыв до цели, дальше которого «больнее» уже не становится.
MAX_GAP = 1.5


def _gap_factor(rating: float) -> float:
    """Насколько владельцу больно. 0 — не болит, 1 — болит максимально."""
    return min(max(TARGET_RATING - rating, 0.0), MAX_GAP) / MAX_GAP


def _volume_factor(review_count: int) -> float:
    """Прокси трафика точки: есть ли кого просить об отзыве.

    Логарифм, потому что разница между 10 и 50 отзывами куда важнее,
    чем между 200 и 300.
    """
    if review_count <= 1:
        return 0.0
    return min(math.log10(review_count) / math.log10(VOLUME_SATURATION), 1.0)


def score_company(company: Company) -> Company:
    """Проставляет score (0-100), verdict и reviews_needed. Меняет объект на месте."""
    if company.rating is None:
        company.score = 0
        company.verdict = "нет данных"
        return company

    gap = _gap_factor(company.rating)
    volume = _volume_factor(company.review_count)
    contact = 1.0 if company.phone else 0.8

    company.score = round(100 * (0.5 * gap + 0.5 * volume) * contact)
    company.reviews_needed = reviews_needed_for(company.rating, company.review_count)

    if company.score >= 70:
        company.verdict = "горячий"
    elif company.score >= 45:
        company.verdict = "рабочий"
    else:
        company.verdict = "холодный"

    return company


def rank(companies: list[Company]) -> list[Company]:
    """Считает скоринг и сортирует по убыванию приоритета."""
    for company in companies:
        score_company(company)
    return sorted(companies, key=lambda c: (c.score, c.review_count), reverse=True)
