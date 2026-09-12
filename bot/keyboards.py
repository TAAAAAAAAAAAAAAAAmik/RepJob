"""Инлайн-клавиатуры."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from leadfinder.presets import PRESETS

NICHE_TITLES = {
    "медицина": "🩺 Медицина",
    "красота": "💇 Красота",
    "авто": "🔧 Авто",
    "спорт": "🏋 Спорт",
    "дети": "🧸 Дети",
    "еда": "☕️ Еда",
    "быт": "🧰 Быт",
}


def niches() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key in PRESETS:
        builder.button(text=NICHE_TITLES.get(key, key), callback_data=f"niche:{key}")
    builder.button(text="🌐 Все ниши сразу", callback_data="niche:__all__")
    builder.adjust(2, 2, 2, 1, 1)
    return builder.as_markup()


def cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отмена", callback_data="cancel"),
    ]])
