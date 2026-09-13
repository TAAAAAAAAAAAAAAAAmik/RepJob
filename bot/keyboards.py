"""Клавиатуры бота — всё управление тапами, без набора команд."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from leadfinder.presets import PRESETS
from leadfinder.sources import SOURCES

# Подписи главного меню. Вынесены в константы, потому что по ним же
# ловятся нажатия в хендлерах — разъедутся, и меню перестанет работать.
FIND = "🔍 Найти клиентов"
CALC = "🧮 Калькулятор"
LAST = "📄 Последняя выдача"
HELP = "❓ Помощь"
ADMIN = "⚙️ Доступы"

NICHE_TITLES = {
    "красота": "💇 Красота",
    "авто": "🔧 Авто",
    "спорт": "🏋 Спорт",
    "дети": "🧸 Дети",
    "еда": "☕️ Еда",
    "быт": "🧰 Быт",
}

CALC_TARGETS = ("4.3", "4.5", "4.7")

# Единственная кнопка, доступная тем, у кого доступа ещё нет.
# Middleware пропускает её по этому значению, поэтому оно — константа.
ACCESS_REQUEST = "req:access"


def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Постоянное меню под полем ввода. Админам — лишний ряд."""
    rows = [
        [KeyboardButton(text=FIND)],
        [KeyboardButton(text=CALC), KeyboardButton(text=LAST)],
    ]
    rows.append([KeyboardButton(text=HELP), KeyboardButton(text=ADMIN)]
                if is_admin else [KeyboardButton(text=HELP)])

    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def cities(recent: list[str]) -> InlineKeyboardMarkup:
    """Недавние города плюс ввод нового.

    В callback_data кладём номер, а не название: у поля лимит 64 байта,
    а кириллица занимает по два байта на букву.
    """
    builder = InlineKeyboardBuilder()
    for index, city in enumerate(recent):
        builder.button(text=f"📍 {city}", callback_data=f"city:{index}")
    builder.button(text="✏️ Другой город", callback_data="city:new")
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def rating_sources(city_known: bool = True) -> InlineKeyboardMarkup:
    """Где смотрим рейтинг.

    Недоступные площадки показываем тоже. Если просто убрать Яндекс из
    списка, вопрос «а почему его нет» возвращается каждую неделю —
    пусть кнопка будет и отвечает сама.
    """
    builder = InlineKeyboardBuilder()
    for source in SOURCES.values():
        builder.button(text=source.title, callback_data=f"src:{source.key}")
    if city_known:
        builder.button(text="↩️ Сменить город", callback_data="back:city")
    builder.adjust(1)
    return builder.as_markup()


def niches(city_known: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key in PRESETS:
        builder.button(text=NICHE_TITLES.get(key, key), callback_data=f"niche:{key}")
    builder.button(text="✏️ Своя ниша", callback_data="niche:__custom__")
    builder.button(text="🌐 Все ниши сразу", callback_data="niche:__all__")
    builder.button(text="↩️ Сменить площадку", callback_data="back:src")
    if city_known:
        builder.button(text="↩️ Сменить город", callback_data="back:city")
    builder.adjust(2, 2, 2, 1, 1, 1, 1)
    return builder.as_markup()


def calc_targets() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for target in CALC_TARGETS:
        builder.button(text=f"до {target}", callback_data=f"target:{target}")
    builder.adjust(3)
    return builder.as_markup()


def pager(page: int, pages: int) -> InlineKeyboardMarkup:
    """Листалка под выдачей плюс что делать дальше.

    Стрелку на краю списка не рисуем: кнопка, которая ничего не делает,
    читается как поломка. Счётчик посередине — не кнопка, но Telegram
    других способов показать позицию не даёт.
    """
    builder = InlineKeyboardBuilder()
    row = 0

    if pages > 1:
        if page > 0:
            builder.button(text="⬅️ Назад", callback_data=f"page:{page - 1}")
            row += 1
        builder.button(text=f"{page + 1}/{pages}", callback_data="page:noop")
        row += 1
        if page < pages - 1:
            builder.button(text="Далее ➡️", callback_data=f"page:{page + 1}")
            row += 1

    builder.button(text="📄 Всё файлом", callback_data="result:csv")
    builder.button(text="🔁 Другая ниша", callback_data="result:again")

    builder.adjust(*([row, 2] if row else [2]))
    return builder.as_markup()


def back_to_sources() -> InlineKeyboardMarkup:
    """Единственный выход из объяснения, почему площадка не ищется."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="↩️ Выбрать другую", callback_data="back:src"),
    ]])


def cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отмена", callback_data="cancel"),
    ]])


# ---------------------------------------------------------------- админка


def admin_panel(users: list[tuple[int, dict]]) -> InlineKeyboardMarkup:
    """Список выданных доступов: тап по строке — отозвать."""
    builder = InlineKeyboardBuilder()

    for user_id, info in users:
        label = info.get("name") or info.get("username") or str(user_id)
        builder.button(text=f"🚫 {label}", callback_data=f"revoke:{user_id}")

    builder.button(text="➕ Выдать доступ", callback_data="admin:grant")
    builder.button(text="🔄 Обновить", callback_data="admin:refresh")
    builder.adjust(1)
    return builder.as_markup()


def confirm_revoke(user_id: int, label: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=f"Да, забрать у {label}"[:60], callback_data=f"revoke_yes:{user_id}")
    builder.button(text="Отмена", callback_data="admin:refresh")
    builder.adjust(1)
    return builder.as_markup()


def grant_request(user_id: int) -> InlineKeyboardMarkup:
    """Кнопка под уведомлением о том, что кто-то постучался в бота."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Выдать доступ", callback_data=f"grant:{user_id}")
    builder.button(text="🚷 Не сейчас", callback_data="grant:no")
    builder.adjust(2)
    return builder.as_markup()


def request_access() -> InlineKeyboardMarkup:
    """То, что видит человек без доступа."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🙋 Хочу доступ", callback_data=ACCESS_REQUEST),
    ]])
