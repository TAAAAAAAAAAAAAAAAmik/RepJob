"""Хендлеры бота. Всё управление кнопками, команды оставлены как ярлыки."""

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

from . import formatting, keyboards as kb, search
from .config import Config
from .storage import Storage

log = logging.getLogger(__name__)
router = Router()

# Нажатия меню не должны утекать в поля ввода мастеров: без этого
# тап по «Калькулятору» посреди ввода города примется за название города.
MENU_LABELS = frozenset({kb.FIND, kb.CALC, kb.LAST, kb.HELP})
NOT_MENU = ~F.text.in_(MENU_LABELS)

# Телеграм режет сообщения на 4096 символах — держимся ниже с запасом
MESSAGE_LIMIT = 3900

# Последний результат на пользователя, чтобы отдать CSV повторно
_last_results: dict[int, search.SearchResult] = {}

HELP = """<b>Поиск клиентов на работу с репутацией</b>

<b>{find}</b> — подобрать компании: город, потом ниша кнопками.
Города запоминаются, повторный поиск в два тапа.

<b>{calc}</b> — сколько пятёрок нужно до цели и за сколько месяцев.
Самое полезное на встрече: считает при клиенте за пару секунд.

<b>{last}</b> — прислать последнюю выдачу файлом ещё раз.

Отбираются компании с рейтингом {rmin}–{rmax} и минимум {reviews} отзывами: \
ниже — обычно реально плохой сервис, выше — у владельца не болит.

Есть и команды, если так быстрее: /find, /calc, /csv, /id"""


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


class Flow(StatesGroup):
    city = State()      # ждём название города текстом
    calc = State()      # ждём «рейтинг отзывов»
    target = State()    # ждём выбор цели кнопкой


# ------------------------------------------------------------------- вход


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Готов искать клиентов. Пользуйся кнопками снизу.",
        reply_markup=kb.main_menu(),
    )


@router.message(Command("help"))
@router.message(F.text == kb.HELP)
async def show_help(message: Message) -> None:
    await message.answer(_help_text(), reply_markup=kb.main_menu())


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>")


# ------------------------------------------------------------------ поиск


@router.message(Command("find"))
@router.message(F.text == kb.FIND)
async def start_find(message: Message, state: FSMContext, storage: Storage) -> None:
    await state.clear()
    await _ask_city(message, state, storage, message.from_user.id)


async def _ask_city(
    message: Message, state: FSMContext, storage: Storage, user_id: int,
) -> None:
    recent = storage.recent_cities(user_id)

    if recent:
        await message.answer("В каком городе ищем?", reply_markup=kb.cities(recent))
        return

    await state.set_state(Flow.city)
    await message.answer("В каком городе ищем? Напиши название.", reply_markup=kb.cancel())


@router.callback_query(F.data.startswith("city:"))
async def picked_city(
    callback: CallbackQuery, state: FSMContext, storage: Storage,
) -> None:
    await callback.answer()
    choice = callback.data.split(":", 1)[1]

    if choice == "new":
        await state.set_state(Flow.city)
        await callback.message.edit_text("Напиши название города.")
        return

    recent = storage.recent_cities(callback.from_user.id)
    try:
        city = recent[int(choice)]
    except (ValueError, IndexError):
        await callback.message.edit_text("Список городов обновился. Нажми «Найти клиентов» заново.")
        return

    await state.update_data(city=city)
    await callback.message.edit_text(
        f"Город: <b>{city}</b>\nТеперь ниша:", reply_markup=kb.niches(),
    )


@router.message(Flow.city, F.text, NOT_MENU)
async def typed_city(message: Message, state: FSMContext) -> None:
    city = (message.text or "").strip()
    if not city or city.startswith("/"):
        await message.answer("Напиши название города, например: Казань")
        return

    await state.update_data(city=city)
    await message.answer(f"Город: <b>{city}</b>\nТеперь ниша:", reply_markup=kb.niches())


@router.callback_query(F.data == "back:city")
async def back_to_city(callback: CallbackQuery, state: FSMContext, storage: Storage) -> None:
    await callback.answer()
    await state.clear()

    recent = storage.recent_cities(callback.from_user.id)
    if recent:
        await callback.message.edit_text("В каком городе ищем?", reply_markup=kb.cities(recent))
    else:
        await state.set_state(Flow.city)
        await callback.message.edit_text("Напиши название города.")


@router.callback_query(F.data.startswith("niche:"))
async def picked_niche(
    callback: CallbackQuery, state: FSMContext, config: Config, storage: Storage,
) -> None:
    await callback.answer()

    data = await state.get_data()
    city = data.get("city")
    if not city:
        await callback.message.answer("Потерял город. Нажми «Найти клиентов» заново.")
        await state.clear()
        return

    niche = callback.data.split(":", 1)[1]
    await state.clear()
    storage.remember_city(callback.from_user.id, city)

    if callback.message.reply_markup:
        await callback.message.edit_reply_markup(reply_markup=None)

    await _run_search(callback.message, config, city, niche, user_id=callback.from_user.id)


@router.callback_query(F.data == "result:again")
async def search_again(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()

    result = _last_results.get(callback.from_user.id)
    if result is None:
        await callback.message.answer("Сначала найди клиентов.")
        return

    await state.update_data(city=result.city)
    await callback.message.answer(
        f"Город: <b>{result.city}</b>\nВыбери нишу:", reply_markup=kb.niches(),
    )


# ------------------------------------------------------------- калькулятор


@router.message(Command("calc"))
@router.message(F.text == kb.CALC)
async def start_calc(message: Message, state: FSMContext) -> None:
    parts = (message.text or "").split()[1:]

    # /calc 3.4 48 — минуя мастер
    if len(parts) >= 2:
        parsed = _parse_calc(parts)
        if parsed is None:
            await message.answer("Не понял числа. Пример: <code>/calc 3.4 48</code>")
            return
        rating, count, target = parsed
        await _send_calc(message, rating, count, target)
        return

    await state.set_state(Flow.calc)
    await message.answer(
        "Напиши текущий рейтинг и число отзывов через пробел.\n"
        "Например: <code>3.4 48</code>",
        reply_markup=kb.cancel(),
    )


@router.message(Flow.calc, F.text, NOT_MENU)
async def got_calc_input(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    parsed = _parse_calc(text.replace(",", ".").split())
    if parsed is None:
        await message.answer(
            "Нужны два числа через пробел: рейтинг и сколько отзывов.\n"
            "Например: <code>3.4 48</code>"
        )
        return

    rating, count, _ = parsed
    await state.update_data(rating=rating, count=count)
    await state.set_state(Flow.target)
    await message.answer(
        f"<b>{rating}</b> при {count} отзывах. До какой оценки ведём?",
        reply_markup=kb.calc_targets(),
    )


@router.callback_query(F.data.startswith("target:"))
async def picked_target(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()

    data = await state.get_data()
    rating, count = data.get("rating"), data.get("count")
    if rating is None or count is None:
        await callback.message.answer("Данные потерялись. Нажми «Калькулятор» заново.")
        await state.clear()
        return

    target = float(callback.data.split(":", 1)[1])
    await state.clear()

    if callback.message.reply_markup:
        await callback.message.edit_reply_markup(reply_markup=None)

    await _send_calc(callback.message, rating, count, target)


def _parse_calc(parts: list[str]) -> tuple[float, int, float] | None:
    """Разбирает «3.4 48» или «3,4 48 4.3». Возвращает None, если не вышло."""
    if len(parts) < 2:
        return None
    try:
        rating = float(parts[0].replace(",", "."))
        count = int(parts[1])
        target = float(parts[2].replace(",", ".")) if len(parts) > 2 else 4.5
    except ValueError:
        return None

    if not (1 <= rating <= 5) or not (1 <= target <= 5) or count < 1:
        return None
    return rating, count, target


async def _send_calc(message: Message, rating: float, count: int, target: float) -> None:
    needed = reviews_needed_for(rating, count, target)
    await message.answer(
        formatting.calc_message(rating, count, target, needed),
        reply_markup=kb.main_menu(),
    )


# ------------------------------------------------------------------ выгрузка


@router.message(Command("csv"))
@router.message(F.text == kb.LAST)
async def send_last(message: Message) -> None:
    result = _last_results.get(message.from_user.id)
    if result is None or not result.companies:
        await message.answer("Ещё нечего выгружать — сначала найди клиентов.")
        return
    await _send_csv(message, result)


@router.callback_query(F.data == "result:csv")
async def resend_csv(callback: CallbackQuery) -> None:
    await callback.answer()
    result = _last_results.get(callback.from_user.id)
    if result is None or not result.companies:
        await callback.message.answer("Ещё нечего выгружать.")
        return
    await _send_csv(callback.message, result)


@router.callback_query(F.data == "cancel")
async def cancelled(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменил")
    await callback.message.edit_text("Отменил.")


# -------------------------------------------------------------------- поиск


async def _run_search(
    message: Message,
    config: Config,
    city: str,
    niche: str,
    user_id: int | None = None,
) -> None:
    user_id = user_id or message.chat.id
    status = await message.answer(f"Ищу в городе <b>{city}</b>… это займёт до минуты.")

    try:
        result = await search.find(
            config.dgis_api_key,
            city,
            niche,
            max_pages=config.max_pages,
            max_results=config.max_results,
            yandex_key=config.yandex_api_key,
        )
    except dgis.DgisError as exc:
        await status.edit_text(f"2GIS ответил ошибкой:\n<code>{exc}</code>")
        return
    except Exception:
        log.exception("Поиск упал: город=%s ниша=%s", city, niche)
        await status.edit_text("Что-то пошло не так. Загляни в логи бота.")
        return

    _last_results[user_id] = result

    await status.edit_text(
        _fit(result),
        disable_web_page_preview=True,
        reply_markup=kb.results() if result.companies else None,
    )

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
        find=kb.FIND, calc=kb.CALC, last=kb.LAST,
        rmin=search.RATING_MIN,
        rmax=search.RATING_MAX,
        reviews=search.MIN_REVIEWS,
    )
