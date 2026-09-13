"""Рендер результатов в сообщения Telegram (parse_mode=HTML)."""

from __future__ import annotations

from html import escape

from leadfinder import sources
from leadfinder.models import Company

# Лидов на одну страницу. Пять — примерно экран телефона: пролистывать
# кнопкой удобнее, чем скроллить простыню.
PAGE_SIZE = 5

# Телеграм режет сообщения на 4096 символах — держимся ниже с запасом
MESSAGE_LIMIT = 3900

def plural(n: int, one: str, few: str, many: str) -> str:
    """Русское согласование числительного. «281 пятёрок» бросается в глаза."""
    tail = abs(n) % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


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
        need = company.reviews_needed
        stats += f" · +{need} {plural(need, 'пятёрка', 'пятёрки', 'пятёрок')} до 4.5"

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


def _queries_line(queries: list[str], limit: int = 3) -> str:
    """Ниша в шапке. Пресет — это девять формулировок, все в строку не нужны."""
    if len(queries) <= limit:
        return ", ".join(queries)
    return ", ".join(queries[:limit]) + f" и ещё {len(queries) - limit}"


def total_pages(count: int, page_size: int = PAGE_SIZE) -> int:
    if count <= 0:
        return 1
    return (count + page_size - 1) // page_size


def clamp_page(page: int, count: int, page_size: int = PAGE_SIZE) -> int:
    """Страница всегда в границах.

    Кнопка под старой выдачей живёт вечно: нажать «далее» на 11-й странице
    можно и тогда, когда новый поиск вернул три компании.
    """
    return max(0, min(page, total_pages(count, page_size) - 1))


def page_message(
    companies: list[Company],
    city: str,
    queries: list[str],
    page: int = 0,
    source: str = sources.DEFAULT,
    page_size: int = PAGE_SIZE,
    limit: int = MESSAGE_LIMIT,
) -> str:
    """Одна страница выдачи: шапка со сводкой плюс несколько лидов."""
    caption = sources.title_of(source)

    if not companies:
        return (
            f"По запросу <b>{escape(city)}</b> под фильтр не попал никто "
            f"(рейтинг смотрели: {escape(caption)}).\n\n"
            "Попробуй другую нишу, другую площадку — по организации в сетях "
            "цифры ниже — или снизь порог отзывов настройкой "
            "<code>BOT_MIN_REVIEWS</code>."
        )

    page = clamp_page(page, len(companies), page_size)
    start = page * page_size
    chunk = companies[start:start + page_size]

    hot = sum(1 for c in companies if c.verdict == "горячий")
    work = sum(1 for c in companies if c.verdict == "рабочий")
    with_phone = sum(1 for c in companies if c.phone)
    avg = sum(c.rating for c in companies if c.rating) / len(companies)

    head = "\n".join([
        f"🎯 <b>{escape(city)}</b> — {escape(_queries_line(queries))}",
        # Площадку называем прямо в шапке: с этим числом идти на звонок,
        # и владелец первым делом спросит, где мы его взяли.
        f"Рейтинг: <b>{escape(caption)}</b> · с телефоном {with_phone} · "
        f"в среднем {avg:.2f}",
        "",
        f"Отобрано <b>{len(companies)}</b>   🔥 {hot}   🟢 {work}",
        f"Показаны <b>{start + 1}–{start + len(chunk)}</b>",
        "",
        "",
    ])

    blocks = [lead_line(c, start + i + 1) for i, c in enumerate(chunk)]
    text = head + "\n\n".join(blocks)

    # Страховка на случай неправдоподобно длинных названий: выкидываем
    # лид целиком, а не режем по символу — иначе порвётся HTML-разметка.
    while len(text) > limit and len(blocks) > 1:
        blocks.pop()
        text = head + "\n\n".join(blocks)

    return text


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
