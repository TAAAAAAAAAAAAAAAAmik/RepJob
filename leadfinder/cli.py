"""Командный интерфейс: собрать лидов по городу и нишам."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import dgis, export, presets, scoring, sources, yandex
from .models import Company, is_target

DEMO_FIXTURE = Path(__file__).parent / "data" / "demo_2gis.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="leadfinder",
        description="Ищет компании с проседающим рейтингом — кандидатов на работу с репутацией.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  python -m leadfinder --city Казань --niche красота авто\n"
            "  python -m leadfinder --city Уфа --niche красота --yandex --out leads.csv\n"
            "  python -m leadfinder --demo\n\n"
            "Пресеты ниш: " + ", ".join(presets.PRESETS)
        ),
    )

    parser.add_argument("--city", help="Город, например: Казань")
    parser.add_argument(
        "--niche",
        nargs="+",
        default=["красота"],
        help="Пресеты ниш или произвольные запросы через пробел",
    )
    parser.add_argument(
        "--rating-source", default=sources.DEFAULT,
        choices=[s.key for s in sources.searchable()],
        help="Где смотреть рейтинг: 2gis — карточка филиала, 2gis_org — организация целиком",
    )
    parser.add_argument("--rating-min", type=float, default=3.0, help="Нижняя граница рейтинга (по умолчанию 3.0)")
    parser.add_argument("--rating-max", type=float, default=4.2, help="Верхняя граница рейтинга (по умолчанию 4.2)")
    parser.add_argument("--min-reviews", type=int, default=10, help="Минимум отзывов, чтобы рейтинг что-то значил")
    parser.add_argument("--require-phone", action="store_true", help="Только компании с телефоном")
    parser.add_argument("--limit", type=int, default=0, help="Оставить только N лучших лидов")
    parser.add_argument("--max-pages", type=int, default=dgis.MAX_PAGES, help="Страниц на один запрос к 2GIS")

    parser.add_argument("--out", default="leads.csv", help="Куда сохранить CSV")
    parser.add_argument("--json", dest="json_out", help="Дополнительно сохранить JSON")

    parser.add_argument("--yandex", action="store_true",
                        help="Подтянуть телефоны и карточки из Яндекса (нужен YANDEX_API_KEY)")
    parser.add_argument("--demo", action="store_true", help="Прогон на встроенном примере, без ключей и сети")
    parser.add_argument("-v", "--verbose", action="store_true", help="Подробный лог")

    return parser


def load_demo() -> list[Company]:
    raw_items = json.loads(DEMO_FIXTURE.read_text(encoding="utf-8"))
    return [dgis.parse_company(raw, city="Демо-город") for raw in raw_items]


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(message)s",
    )

    # ---------------------------------------------------------- сбор данных
    if args.demo:
        print("Демо-режим: встроенный пример, сеть и ключи не нужны.\n")
        companies = load_demo()
    else:
        if not args.city:
            print("Укажи город: --city Казань (или запусти --demo)", file=sys.stderr)
            return 2

        api_key = os.environ.get("DGIS_API_KEY", "")
        if not api_key:
            print(
                "Нет ключа 2GIS. Получи бесплатный на https://dev.2gis.ru и положи в окружение:\n"
                "  export DGIS_API_KEY=твой_ключ",
                file=sys.stderr,
            )
            return 2

        queries = presets.resolve(args.niche)
        print(f"Город: {args.city}")
        print(f"Запросов: {len(queries)} — {', '.join(queries)}\n")

        client = dgis.DgisClient(api_key)
        try:
            companies = dgis.collect(
                client, args.city, queries,
                max_pages=args.max_pages, rating_source=args.rating_source,
            )
        except dgis.DgisError as exc:
            print(f"Ошибка 2GIS: {exc}", file=sys.stderr)
            return 1

        print(f"Найдено всего: {len(companies)}")
        print(f"Потрачено запросов к 2GIS: {client.requests_made}")

    # ------------------------------------------------------------- фильтрация
    selected = [
        company for company in companies
        if is_target(
            company,
            rating_min=args.rating_min,
            rating_max=args.rating_max,
            min_reviews=args.min_reviews,
            require_phone=args.require_phone,
        )
    ]

    selected = scoring.rank(selected)
    if args.limit > 0:
        selected = selected[: args.limit]

    # ------------------------------------------------------------ обогащение
    yandex_client = None
    if args.yandex:
        yandex_key = os.environ.get("YANDEX_API_KEY", "")
        if yandex_key:
            yandex_client = yandex.YandexClient(yandex_key)
            print(f"Спрашиваю телефоны у Яндекса для {len(selected)} компаний…")
        else:
            print("Нет YANDEX_API_KEY — телефонов не будет, поставлю ссылки на поиск.")
    yandex.enrich(selected, yandex_client)
    if yandex_client is not None:
        got = sum(1 for c in selected if c.phone_source == "yandex")
        print(f"Телефонов найдено: {got} из {len(selected)}"
              f" (запросов к Яндексу: {yandex_client.requests_made})")

    # --------------------------------------------------------------- вывод
    print()
    print(export.summary(selected))

    if selected:
        out_path = export.to_csv(selected, args.out)
        print(f"\nCSV: {out_path}")

        if args.json_out:
            json_path = export.to_json(selected, args.json_out)
            print(f"JSON: {json_path}")

        print("\nТоп-5 на сегодня:")
        for company in selected[:5]:
            needed = f"нужно {company.reviews_needed} пятёрок" if company.reviews_needed else "—"
            phone = company.phone or "телефона нет"
            print(f"  [{company.score:>3}] {company.rating} · {company.review_count} отз. · {needed}")
            print(f"        {company.name} — {phone}")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
