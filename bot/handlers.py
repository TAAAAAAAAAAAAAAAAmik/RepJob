"""Хендлеры бота."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message, TelegramObject

from leadfinder import dgis
from leadfinder.models import reviews_needed_for

from . import formatting, keyboards, search
from .config import Config

log = logging.getLogger(__name__)
router = Router()

# Телеграм режет сообщения на 4096 символах — держимся ниже с запасом
MESSAGE_LIMIT = 3900

# Последний результат на пользователя, чтобы отдать CSV повторно
_last_results: dict[int, search.SearchResult] = {}

HELP = """<b>Поиск клиентов на работу с репутацией</b>

/find — подобрать компании в городе
/calc — сколько пятёрок нужно до цели
/csv — прислать последнюю выдачу файлом
/id — узнать свой Telegram ID

<b>Быстрый вызов:</b>
<code>/find Казань медицина</code>
<code>/calc 3.4 48</code> — до 4.5
<code>/calc 3.4 48 4.3</code> — до своей цели

Отбираются компании с рейтингом {rmin}–{rmax} и минимум {reviews} отзывами: \
ниже — обычно реально плохой сервис, выше — у владельца не болит."""


class AccessMiddleware(BaseMiddleware):
    """Пускает только своих. /id доступен всем, чтобы узнать свой номер."""

    def __init__(self, config: Config):
        self.config = config

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        text = getattr(event, "text", "") or ""
        if text.startswith("/id"):
            return await handler(event, data)

        if not self.config.access_configured:
            await self._deny(
                event,
                "Доступ к боту пока никому не выдан.\n\n"
                f"Твой Telegram ID: <code>{user.id}</code>\n"
                "Добавь его в <code>BOT_ALLOWED_IDS</code> и перезапусти бота.",
            )
            return None

        if not self.config.is_allowed(user.id):
            log.warning("Отказано в доступе: %s (@%s)", user.id, user.username)
            await self._deny(event, "Этот бот закрытый.")
            return None

        return await handler(event, data)

    @staticmethod
    async def _deny(event: TelegramObject, text: str) -> None:
        if isinstance(event, Message):
            await event.answer(text)
        elif isinstance(event, CallbackQuery):
            await event.answer(text.split("\n")[0], show_alert=True)


class Search(StatesGroup):
    city = State()
    niche = State()


# ------------------------------------------------------------------ команды


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Готов искать клиентов.\n\n" + _help_text(),
        disable_web_page_preview=True,
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(_help_text(), disable_web_page_preview=True)


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    user = message.from_user
    await message.answer(f"Твой Telegram ID: <code>{user.id}</code>")


@router.message(Command("calc"))
async def cmd_calc(message: Message) -> None:
    parts = (message.text or "").split()[1:]

    if len(parts) < 2:
        await message.answer(
            "Как пользоваться:\n"
            "<code>/calc 3.4 48</code> — рейтинг, отзывов, цель 4.5\n"
            "<code>/calc 3.4 48 4.3</code> — со своей целью"
        )
        return

    try:
        rating = float(parts[0].replace(",", "."))
        count = int(parts[1])
        target = float(parts[2].replace(",", ".")) if len(parts) > 2 else 4.5
    except ValueError:
        await message.answer("Не понял числа. Пример: <code>/calc 3.4 48</code>")
        return

    if not (1 <= rating <= 5) or not (1 <= target <= 5) or count < 1:
        await message.answer("Рейтинг от 1 до 5, отзывов — хотя бы один.")
        return

    needed = reviews_needed_for(rating, count, target)
    await message.answer(formatting.calc_message(rating, count, target, needed))


@router.message(Command("csv"))
async def cmd_csv(message: Message) -> None:
    result = _last_results.get(message.from_user.id)
    if result is None or not result.companies:
        await message.answer("Ещё нечего выгружать — сначала /find")
        return

    await _send_csv(message, result)


@router.message(Command("find"))
async def cmd_find(message: Message, state: FSMContext, config: Config) -> None:
    parts = (message.text or "").split()[1:]

    # /find Казань медицина — сразу, без мастера
    if len(parts) >= 2:
        city, niche = parts[0], parts[1].casefold()
        await state.clear()
        await _run_search(message, config, city, niche)
        return

    if len(parts) == 1:
        await state.update_data(city=parts[0])
        await state.set_state(Search.niche)
        await message.answer(f"Город: <b>{parts[0]}</b>\nТеперь ниша:", reply_markup=keyboards.niches())
        return

    await state.set_state(Search.city)
    await message.answer("В каком городе ищем?", reply_markup=keyboards.cancel())


@router.message(Search.city, F.text)
async def got_city(message: Message, state: FSMContext) -> None:
    city = (message.text or "").strip()
    if not city or city.startswith("/"):
        await message.answer("Напиши название города, например: Казань")
        return

    await state.update_data(city=city)
    await state.set_state(Search.niche)
    await message.answer(f"Город: <b>{city}</b>\nТеперь ниша:", reply_markup=keyboards.niches())


@router.callback_query(F.data.startswith("niche:"))
async def got_niche(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    await callback.answer()

    data = await state.get_data()
    city = data.get("city")
    if not city:
        await callback.message.answer("Потерял город. Начни заново: /find")
        await state.clear()
        return

    niche = callback.data.split(":", 1)[1]
    await state.clear()

    if callback.message.reply_markup:
        await callback.message.edit_reply_markup(reply_markup=None)

    await _run_search(callback.message, config, city, niche, user_id=callback.from_user.id)


@router.callback_query(F.data == "cancel")
async def cancelled(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменил")
    await callback.message.edit_text("Отменил. Начать заново: /find")


# -------------------------------------------------------------------- поиск


async def _run_search(
    message: Message,
    config: Config,
    city: str,
    niche: str,
    user_id: int | None = None,
) -> None:
    user_id = user_id or message.from_user.id
    status = await message.answer(f"Ищу в городе <b>{city}</b>… это займёт до минуты.")

    try:
        result = await search.find(
            config.dgis_api_key,
            city,
            niche,
            max_pages=config.max_pages,
            max_results=config.max_results,
        )
    except dgis.DgisError as exc:
        await status.edit_text(f"2GIS ответил ошибкой:\n<code>{exc}</code>")
        return
    except Exception:
        log.exception("Поиск упал: город=%s ниша=%s", city, niche)
        await status.edit_text("Что-то пошло не так. Загляни в логи бота.")
        return

    _last_results[user_id] = result

    text = _fit(result)
    await status.edit_text(text, disable_web_page_preview=True)

    if result.companies:
        await _send_csv(message, result)


def _fit(result: search.SearchResult) -> str:
    """Подбирает размер топа так, чтобы сообщение влезло в лимит Telegram."""
    for top in (10, 7, 5, 3, 1):
        text = formatting.results_message(result.companies, result.city, result.queries, top=top)
        if len(text) <= MESSAGE_LIMIT:
            return text
    return formatting.results_message(result.companies, result.city, result.queries, top=1)[:MESSAGE_LIMIT]


async def _send_csv(message: Message, result: search.SearchResult) -> None:
    payload = search.to_csv_bytes(result.companies)
    safe_city = "".join(ch for ch in result.city if ch.isalnum() or ch in "-_") or "leads"
    document = BufferedInputFile(payload, filename=f"leads_{safe_city}.csv")

    await message.answer_document(
        document,
        caption=f"{len(result.companies)} компаний. Открывается в Excel двойным кликом.",
    )


def _help_text() -> str:
    return HELP.format(
        rmin=search.RATING_MIN,
        rmax=search.RATING_MAX,
        reviews=search.MIN_REVIEWS,
    )
