"""Рендер результатов в сообщения Telegram (parse_mode=HTML)."""

from __future__ import annotations

from html import escape

from leadfinder.models import Company

VERDICT_MARK = {
    "горячий": "🔥",
    "рабочий": "🟢",
    "холодный": "⚪",
}


def lead_line(company: Company, index: int) -> str:
    """Одна компания в списке — компактно, но со всем нужным для звонка."""
    mark = VERDICT_MARK.get(company.verdict, "•")
    name = escape(company.name)

    head = f"{index}. {mark} <b>{name}</b>"
    stats = f"   {company.rating} · {company.review_count} отз."
    if company.reviews_needed:
        stats += f" · нужно {company.reviews_needed} пятёрок до 4.5"

    lines = [head, stats]

    if company.phone:
        # tel: делает номер кликабельным на телефоне
        lines.append(f"   📞 <a href=\"tel:{escape(company.phone)}\">{escape(company.phone)}</a>")
    else:
        lines.append("   📞 телефона нет")

    if company.address:
        lines.append(f"   📍 {escape(company.address)}")

    links = []
    if company.url_2gis:
        links.append(f"<a href=\"{escape(company.url_2gis)}\">2GIS</a>")
    if company.url_yandex:
        links.append(f"<a href=\"{escape(company.url_yandex)}\">Яндекс</a>")
    if links:
        lines.append("   " + " · ".join(links))

    return "\n".join(lines)


def results_message(companies: list[Company], city: str, queries: list[str], top: int = 10) -> str:
    """Сводка плюс верхушка списка."""
    if not companies:
        return (
            f"По запросу <b>{escape(city)}</b> под фильтр не попал никто.\n\n"
            "Попробуй расширить диапазон: <code>/find</code> и выбрать другую нишу, "
            "или снизить порог отзывов настройкой <code>BOT_MIN_REVIEWS</code>."
        )

    hot = sum(1 for c in companies if c.verdict == "горячий")
    work = sum(1 for c in companies if c.verdict == "рабочий")
    with_phone = sum(1 for c in companies if c.phone)
    avg = sum(c.rating for c in companies if c.rating) / len(companies)

    header = [
        f"🎯 <b>{escape(city)}</b> — {escape(', '.join(queries))}",
        "",
        f"Отобрано: <b>{len(companies)}</b>   🔥 {hot}   🟢 {work}",
        f"С телефоном: {with_phone} · средний рейтинг {avg:.2f}",
        "",
        f"<b>Топ-{min(top, len(companies))}:</b>",
        "",
    ]

    body = "\n\n".join(lead_line(c, i) for i, c in enumerate(companies[:top], start=1))

    footer = ""
    if len(companies) > top:
        footer = f"\n\nОстальные {len(companies) - top} — в файле ниже."

    return "\n".join(header) + body + footer


def calc_message(rating: float, count: int, target: float, needed: int | None) -> str:
    if needed is None:
        return "Цель 5.0 недостижима, пока на карточке есть хоть одна оценка ниже пяти. Реальный потолок — 4.8–4.9."
    if needed == 0:
        return f"Карточка уже на {rating}, цель {target} достигнута."

    lines = [
        f"<b>{rating}</b> при {count} отзывах → цель <b>{target}</b>",
        "",
        f"Нужно пятёрок: <b>{needed}</b>",
    ]

    for flow in (10, 20, 30):
        months = needed / flow
        lines.append(f"   при {flow} отз./мес — {months:.1f} мес")

    lines += [
        "",
        "<i>Расчёт по чистой средней. Свежие отзывы весят больше, "
        "поэтому на практике выходит быстрее.</i>",
    ]
    return "\n".join(lines)
