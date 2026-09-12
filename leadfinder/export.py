"""Выгрузка найденных лидов."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import Company

# Порядок колонок под реальную работу: сначала за кого хвататься,
# потом чем звонить, потом откуда цифры.
COLUMNS: list[tuple[str, str]] = [
    ("score", "Приоритет"),
    ("verdict", "Оценка лида"),
    ("name", "Название"),
    ("rubric", "Рубрика"),
    ("rating", "Рейтинг 2GIS"),
    ("review_count", "Отзывов"),
    ("reviews_needed", "Нужно пятёрок до 4.5"),
    ("phone", "Телефон"),
    ("address", "Адрес"),
    ("website", "Сайт"),
    ("url_2gis", "Карточка 2GIS"),
    ("url_yandex", "Карточка Яндекс"),
    ("city", "Город"),
    ("source_id", "ID источника"),
]


def to_csv(companies: list[Company], path: str | Path) -> Path:
    """Пишет CSV, который корректно открывается в Excel.

    BOM обязателен: без него русский Excel читает UTF-8 как кракозябры.
    Разделитель «;» по той же причине — в русской локали Excel ждёт именно его.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow([title for _, title in COLUMNS])

        for company in companies:
            row = company.as_row()
            writer.writerow(["" if row.get(key) is None else row.get(key) for key, _ in COLUMNS])

    return path


def to_json(companies: list[Company], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [company.as_row() for company in companies]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def summary(companies: list[Company]) -> str:
    """Короткая сводка в консоль после прогона."""
    if not companies:
        return "Под фильтр не попал никто. Попробуй расширить диапазон рейтинга или снизить порог отзывов."

    hot = sum(1 for c in companies if c.verdict == "горячий")
    work = sum(1 for c in companies if c.verdict == "рабочий")
    cold = sum(1 for c in companies if c.verdict == "холодный")
    with_phone = sum(1 for c in companies if c.phone)
    avg_rating = sum(c.rating for c in companies if c.rating) / len(companies)

    return (
        f"Отобрано: {len(companies)}\n"
        f"  горячих:  {hot}\n"
        f"  рабочих:  {work}\n"
        f"  холодных: {cold}\n"
        f"  с телефоном: {with_phone}\n"
        f"  средний рейтинг по выборке: {avg_rating:.2f}"
    )
