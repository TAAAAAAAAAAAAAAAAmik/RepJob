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


def niches(city_known: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key in PRESETS:
        builder.button(text=NICHE_TITLES.get(key, key), callback_data=f"niche:{key}")
    builder.button(text="🌐 Все ниши сразу", callback_data="niche:__all__")
    if city_known:
        builder.button(text="↩️ Сменить город", callback_data="back:city")
    builder.adjust(2, 2, 2, 1, 1)
    return builder.as_markup()


def calc_targets() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for target in CALC_TARGETS:
        builder.button(text=f"до {target}", callback_data=f"target:{target}")
    builder.adjust(3)
    return builder.as_markup()


def results(has_more: bool = False) -> InlineKeyboardMarkup:
    """Что можно сделать сразу после выдачи."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📄 Прислать CSV", callback_data="result:csv")
    builder.button(text="🔁 Другая ниша", callback_data="result:again")
    builder.adjust(2)
    return builder.as_markup()


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
