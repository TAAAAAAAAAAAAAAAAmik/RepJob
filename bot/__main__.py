"""Точка входа: python -m bot"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from . import config as config_module
from .handlers import AccessMiddleware, router
from .storage import Storage


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands([
        BotCommand(command="start", description="Меню"),
        BotCommand(command="find", description="Подобрать компании в городе"),
        BotCommand(command="calc", description="Сколько пятёрок нужно до цели"),
        BotCommand(command="csv", description="Последняя выдача файлом"),
        BotCommand(command="help", description="Справка"),
        BotCommand(command="id", description="Мой Telegram ID"),
    ])


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    log = logging.getLogger("bot")

    cfg = config_module.load()

    if not cfg.access_configured:
        log.warning(
            "BOT_ALLOWED_IDS пуст — бот никого не пустит. "
            "Напиши ему /id, добавь свой номер в переменную и перезапусти."
        )

    session = AiohttpSession(proxy=cfg.proxy) if cfg.proxy else None
    if cfg.proxy:
        log.info("Хожу в Telegram через прокси")

    bot = Bot(
        cfg.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())

    middleware = AccessMiddleware(cfg)
    dispatcher.message.outer_middleware(middleware)
    dispatcher.callback_query.outer_middleware(middleware)

    dispatcher.include_router(router)

    await set_commands(bot)

    me = await bot.get_me()
    log.info("Запущен как @%s", me.username)

    # Пропускаем накопившиеся за простой апдейты, чтобы бот не разгребал очередь
    await bot.delete_webhook(drop_pending_updates=True)
    await dispatcher.start_polling(bot, config=cfg, storage=Storage())


def main() -> None:
    # SystemExit не перехватываем: в нём лежит объяснение, чего не хватает
    # для запуска, и без него на сервере бот падает молча.
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logging.getLogger("bot").info("Остановлен")


if __name__ == "__main__":
    main()
