import asyncio
import logging

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config.settings import settings
from db.database import init_db, create_tables
from bot.middlewares.middlewares import DatabaseMiddleware, BanCheckMiddleware
from bot.handlers.user import start, payment, support, mtproto
from bot.handlers.admin import admin
from bot.handlers.webhook import create_webhook_app
from bot.services.scheduler import scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def main():
    init_db(settings.DATABASE_URL)
    await create_tables()

    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.middleware(DatabaseMiddleware())
    dp.callback_query.middleware(DatabaseMiddleware())
    dp.inline_query.middleware(DatabaseMiddleware())
    dp.message.middleware(BanCheckMiddleware())
    dp.callback_query.middleware(BanCheckMiddleware())

    dp.include_router(start.router)
    dp.include_router(payment.router)
    dp.include_router(support.router)
    dp.include_router(mtproto.router)
    dp.include_router(admin.router)

    # Запускаем webhook-сервер для событий от Remnawave
    webhook_app = create_webhook_app(bot)
    runner = web.AppRunner(webhook_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.WEBHOOK_PORT)
    await site.start()
    logger.info(f"Remnawave webhook server started on port {settings.WEBHOOK_PORT}")

    # Scheduler — fallback если панель не доставила событие
    asyncio.create_task(scheduler(bot))

    logger.info("Bot started")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
