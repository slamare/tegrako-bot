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


def _make_bot() -> Bot:
    proxy_url = settings.TELEGRAM_BOT_PROXY
    if proxy_url:
        try:
            from aiohttp_socks import ProxyConnector
            from aiogram.client.session.aiohttp import AiohttpSession

            connector = ProxyConnector.from_url(proxy_url)

            class ProxiedSession(AiohttpSession):
                def create_connector(self, app=None):
                    return connector

            bot = Bot(token=settings.BOT_TOKEN, session=ProxiedSession())
            logger.info(f"Telegram bot proxy: {proxy_url}")
            return bot
        except Exception as e:
            logger.error(f"Proxy setup failed, running without proxy: {e}")
    return Bot(token=settings.BOT_TOKEN)


async def main():
    init_db(settings.DATABASE_URL)
    await create_tables()

    bot = _make_bot()
    dp = Dispatcher(storage=MemoryStorage())

    for mw in (DatabaseMiddleware(), BanCheckMiddleware()):
        dp.message.middleware(mw)
        dp.callback_query.middleware(mw)
    dp.message.middleware(DatabaseMiddleware())
    dp.inline_query.middleware(DatabaseMiddleware())
    dp.inline_query.middleware(BanCheckMiddleware())

    dp.include_router(start.router)
    dp.include_router(payment.router)
    dp.include_router(support.router)
    dp.include_router(mtproto.router)
    dp.include_router(admin.router)

    webhook_app = create_webhook_app(bot)
    runner = web.AppRunner(webhook_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", settings.WEBHOOK_PORT).start()
    logger.info(f"Webhook server on port {settings.WEBHOOK_PORT}")

    asyncio.create_task(scheduler(bot))
    logger.info("Bot started")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
