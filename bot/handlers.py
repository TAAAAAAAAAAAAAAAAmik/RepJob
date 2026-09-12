"""Хендлеры бота. Всё управление кнопками, команды оставлены как ярлыки."""

from __future__ import annotations

import logging
from datetime import datetime
from html import escape
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
MENU_LABELS = frozenset({kb.FIND, kb.CALC, kb.LAST, kb.HELP, kb.ADMIN})
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
    """Пускает админов, выданные доступы и жёстко заданные в окружении.

    Остальным отказывает и сообщает админам, кто постучался: просить
    у человека его ID и вписывать руками — лишний шаг.
    """

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

        storage: Storage | None = data.get("storage")

        if self.config.is_allowed(user.id) or (storage and storage.is_allowed(user.id)):
            return await handler(event, data)

        if not self.config.access_configured:
            await self._deny(
                event,
                "Доступ к боту пока никому не выдан.\n\n"
                f"Твой Telegram ID: <code>{user.id}</code>\n"
                "Добавь его в <code>BOT_ADMIN_IDS</code> и перезапусти бота.",
            )
            return None

        log.warning("Отказано в доступе: %s (@%s)", user.id, user.username)
        await self._deny(event, "Бот закрытый. Владельцу отправлен запрос — дождись ответа.")
        await self._notify_admins(data.get("bot"), storage, user)
        return None

    async def _notify_admins(self, bot: Any, storage: Storage | None, user: Any) -> None:
        if bot is None or storage is None or not self.config.admin_ids:
            return

        name = getattr(user, "full_name", "") or ""
        username = getattr(user, "username", "") or ""

        # Один и тот же человек не должен дёргать админа каждым сообщением
        if not storage.should_notify(user.id, name, username):
            return

        nick = f"@{username}" if username else "без ника"
        text = (
            "🔔 В бота постучались\n\n"
            f"<b>{escape(name) or 'без имени'}</b> ({escape(nick)})\n"
            f"ID: <code>{user.id}</code>"
        )

        for admin_id in self.config.admin_ids:
            try:
                await bot.send_message(admin_id, text, reply_markup=kb.grant_request(user.id))
            except Exception:
                log.warning("Не смог уведомить админа %s", admin_id)

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
    grant = State()     # ждём ID или пересланное сообщение
    custom = State()    # ждём свою формулировку ниши


# ------------------------------------------------------------------- вход


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, config: Config) -> None:
    await state.clear()
    await message.answer(
        "Готов искать клиентов. Пользуйся кнопками снизу.",
        reply_markup=kb.main_menu(config.is_admin(message.from_user.id)),
    )


@router.message(Command("help"))
@router.message(F.text == kb.HELP)
async def show_help(message: Message, config: Config) -> None:
    await message.answer(
        _help_text(config.is_admin(message.from_user.id)),
        reply_markup=kb.main_menu(config.is_admin(message.from_user.id)),
    )


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

    if niche == "__custom__":
        await state.set_state(Flow.custom)
        await callback.message.edit_text(
            f"Город: <b>{escape(city)}</b>\n\n"
            "Напиши, что искать. Одним словом или фразой, как искал бы в 2GIS:\n"
            "<code>стоматология</code>, <code>ремонт обуви</code>, <code>вет клиника</code>\n\n"
            "Можно несколько через запятую — тогда будет шире, но и запросов уйдёт больше."
        )
        return

    await state.clear()
    storage.remember_city(callback.from_user.id, city)

    if callback.message.reply_markup:
        await callback.message.edit_reply_markup(reply_markup=None)

    await _run_search(callback.message, config, city, niche, user_id=callback.from_user.id)


@router.message(Flow.custom, F.text, NOT_MENU)
async def typed_niche(
    message: Message, state: FSMContext, config: Config, storage: Storage,
) -> None:
    query = (message.text or "").strip()
    if not query or query.startswith("/"):
        await message.answer("Напиши, что искать. Например: <code>стоматология</code>")
        return

    data = await state.get_data()
    city = data.get("city")
    if not city:
        await state.clear()
        await message.answer("Потерял город. Нажми «Найти клиентов» заново.")
        return

    await state.clear()
    storage.remember_city(message.from_user.id, city)
    await _run_search(message, config, city, query, user_id=message.from_user.id)


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
    await message.answer(formatting.calc_message(rating, count, target, needed))


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


def _help_text(is_admin: bool = False) -> str:
    text = HELP.format(
        find=kb.FIND, calc=kb.CALC, last=kb.LAST,
        rmin=search.RATING_MIN,
        rmax=search.RATING_MAX,
        reviews=search.MIN_REVIEWS,
    )
    if is_admin:
        text += (
            f"\n\n<b>{kb.ADMIN}</b> — выдать и забрать доступ. "
            "Когда в бота стучится новый человек, приходит уведомление "
            "с кнопкой: жать её быстрее, чем спрашивать ID."
        )
    return text


# ------------------------------------------------------------------ админка
#
# Права проверяются в каждом обработчике отдельно. Middleware пускает всех
# с доступом, а callback_data видно любому, кто до неё доберётся, — без
# проверки рядовой пользователь мог бы раздавать и отбирать доступы.


def _deny_if_not_admin(user_id: int, config: Config) -> bool:
    if config.is_admin(user_id):
        return False
    log.warning("Попытка залезть в админку: %s", user_id)
    return True


def _user_label(user_id: int, info: dict[str, Any]) -> str:
    name = info.get("name") or ""
    username = info.get("username") or ""
    if name and username:
        return f"{name} (@{username})"
    return name or (f"@{username}" if username else str(user_id))


def _panel_text(config: Config, storage: Storage) -> str:
    users = storage.allowed_users()

    lines = ["<b>Доступы к боту</b>", ""]
    lines.append(f"Админов: {len(config.admin_ids)} — забрать можно только через <code>.env</code>")

    if config.allowed_ids - config.admin_ids:
        lines.append(f"Задано в окружении: {len(config.allowed_ids - config.admin_ids)}")

    lines.append("")
    if not users:
        lines.append("Выданных из чата доступов пока нет.")
        lines.append("Жми «Выдать доступ» или дождись, пока человек напишет боту.")
        return "\n".join(lines)

    lines.append(f"Выдано из чата: <b>{len(users)}</b>")
    lines.append("")
    for user_id, info in users:
        when = info.get("granted_at")
        stamp = datetime.fromtimestamp(when).strftime("%d.%m.%Y") if when else "—"
        lines.append(f"• {escape(_user_label(user_id, info))}")
        lines.append(f"   <code>{user_id}</code> · с {stamp}")
    lines.append("")
    lines.append("Тап по имени — забрать доступ.")
    return "\n".join(lines)


@router.message(Command("admin"))
@router.message(F.text == kb.ADMIN)
async def open_panel(message: Message, state: FSMContext, config: Config, storage: Storage) -> None:
    if _deny_if_not_admin(message.from_user.id, config):
        return
    await state.clear()
    await message.answer(
        _panel_text(config, storage),
        reply_markup=kb.admin_panel(storage.allowed_users()),
    )


@router.callback_query(F.data == "admin:refresh")
async def refresh_panel(callback: CallbackQuery, state: FSMContext, config: Config, storage: Storage) -> None:
    await callback.answer()
    if _deny_if_not_admin(callback.from_user.id, config):
        return
    await state.clear()

    text = _panel_text(config, storage)
    markup = kb.admin_panel(storage.allowed_users())
    # Телеграм ругается, если новый текст совпадает со старым
    if callback.message.html_text != text:
        await callback.message.edit_text(text, reply_markup=markup)
    else:
        await callback.message.edit_reply_markup(reply_markup=markup)


@router.callback_query(F.data == "admin:grant")
async def ask_grant(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    await callback.answer()
    if _deny_if_not_admin(callback.from_user.id, config):
        return

    await state.set_state(Flow.grant)
    await callback.message.answer(
        "Пришли <b>ID</b> человека — или перешли сюда любое его сообщение.\n\n"
        "Свой ID он узнаёт командой <code>/id</code>. Если просто напишет боту, "
        "тебе придёт уведомление с кнопкой, и спрашивать ничего не придётся.",
        reply_markup=kb.cancel(),
    )


@router.message(Flow.grant, F.text, NOT_MENU)
async def grant_by_text(message: Message, state: FSMContext, config: Config, storage: Storage) -> None:
    if _deny_if_not_admin(message.from_user.id, config):
        await state.clear()
        return

    # Пересланное сообщение выдаёт отправителя — если тот не скрыл его настройками
    forwarded = getattr(message, "forward_from", None)
    if forwarded is not None:
        await _do_grant(message, storage, forwarded.id,
                        getattr(forwarded, "full_name", ""), forwarded.username or "",
                        by=message.from_user.id)
        await state.clear()
        return

    raw = (message.text or "").strip()
    if not raw.lstrip("-").isdigit():
        await message.answer(
            "Это не похоже на ID. Нужно число, например <code>6887373040</code>.\n"
            "Либо перешли сюда сообщение от человека."
        )
        return

    info = storage.pending_info(int(raw))
    await _do_grant(message, storage, int(raw),
                    info.get("name", ""), info.get("username", ""),
                    by=message.from_user.id)
    await state.clear()


@router.message(Flow.grant, F.forward_from)
async def grant_by_forward(message: Message, state: FSMContext, config: Config, storage: Storage) -> None:
    if _deny_if_not_admin(message.from_user.id, config):
        await state.clear()
        return

    person = message.forward_from
    await _do_grant(message, storage, person.id,
                    getattr(person, "full_name", ""), person.username or "",
                    by=message.from_user.id)
    await state.clear()


async def _do_grant(
    message: Message, storage: Storage, user_id: int,
    name: str, username: str, by: int,
) -> None:
    storage.grant(user_id, name=name, username=username, by=by)
    label = _user_label(user_id, {"name": name, "username": username})

    await message.answer(f"✅ Доступ выдан: <b>{escape(label)}</b>")

    # Сообщаем человеку сами: иначе он не узнает и будет ждать
    try:
        await message.bot.send_message(
            user_id,
            "Доступ к боту открыт. Нажми /start, чтобы начать.",
        )
    except Exception:
        await message.answer(
            "Предупредить его не смог — он должен сам написать боту хотя бы раз, "
            "иначе Telegram не даёт писать первым."
        )


@router.callback_query(F.data.startswith("grant:"))
async def grant_from_request(callback: CallbackQuery, config: Config, storage: Storage) -> None:
    await callback.answer()
    if _deny_if_not_admin(callback.from_user.id, config):
        return

    choice = callback.data.split(":", 1)[1]
    if choice == "no":
        await callback.message.edit_text("Отклонено. Доступ не выдан.")
        return

    user_id = int(choice)
    info = storage.pending_info(user_id)
    storage.grant(user_id, name=info.get("name", ""), username=info.get("username", ""),
                  by=callback.from_user.id)

    label = _user_label(user_id, info)
    await callback.message.edit_text(f"✅ Доступ выдан: <b>{escape(label)}</b>")

    try:
        await callback.bot.send_message(user_id, "Доступ к боту открыт. Нажми /start, чтобы начать.")
    except Exception:
        log.warning("Не смог сообщить %s о выданном доступе", user_id)


@router.callback_query(F.data.startswith("revoke:"))
async def ask_revoke(callback: CallbackQuery, config: Config, storage: Storage) -> None:
    await callback.answer()
    if _deny_if_not_admin(callback.from_user.id, config):
        return

    user_id = int(callback.data.split(":", 1)[1])
    info = dict(storage.allowed_users()).get(user_id, {})
    label = _user_label(user_id, info)

    await callback.message.edit_text(
        f"Забрать доступ у <b>{escape(label)}</b>?\n<code>{user_id}</code>",
        reply_markup=kb.confirm_revoke(user_id, label),
    )


@router.callback_query(F.data.startswith("revoke_yes:"))
async def do_revoke(callback: CallbackQuery, config: Config, storage: Storage) -> None:
    await callback.answer()
    if _deny_if_not_admin(callback.from_user.id, config):
        return

    user_id = int(callback.data.split(":", 1)[1])
    info = dict(storage.allowed_users()).get(user_id, {})
    label = _user_label(user_id, info)

    if storage.revoke(user_id):
        await callback.message.edit_text(f"🚫 Доступ забран: <b>{escape(label)}</b>")
    else:
        await callback.message.edit_text("Доступа и так не было.")

    await callback.message.answer(
        _panel_text(config, storage),
        reply_markup=kb.admin_panel(storage.allowed_users()),
    )
