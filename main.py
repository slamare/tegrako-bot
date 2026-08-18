import asyncio
import logging
import signal

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage

from config.settings import settings
from db.database import init_db, create_tables, dispose_engine
from bot.middlewares.middlewares import DatabaseMiddleware, BanCheckMiddleware, ThrottlingMiddleware
from bot.handlers.user import start, payment, support, mtproto
from bot.handlers.admin import admin
from bot.handlers.webhook import create_webhook_app
from bot.services.scheduler import scheduler
from bot.services.node_checker import node_checker_loop
from bot.services import remnawave

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _mask_proxy(proxy_url: str) -> str:
    if "@" not in proxy_url:
        return proxy_url
    scheme, _, rest = proxy_url.partition("://")
    _, _, host = rest.partition("@")
    return f"{scheme}://***@{host}"


def _make_bot() -> Bot:
    proxy_url = settings.TELEGRAM_BOT_PROXY
    if proxy_url:
        try:
            session = AiohttpSession(proxy=proxy_url)
            logger.info(f"Telegram bot proxy: {_mask_proxy(proxy_url)}")
            return Bot(token=settings.BOT_TOKEN, session=session)
        except Exception as e:
            logger.error(f"Proxy setup failed, running without proxy: {e}")
    return Bot(token=settings.BOT_TOKEN)


async def main():
    init_db(settings.DATABASE_URL)
    await create_tables()

    bot = _make_bot()
    dp = Dispatcher(storage=MemoryStorage())

    throttling = ThrottlingMiddleware(interval=0.4)
    dp.message.middleware(throttling)
    dp.callback_query.middleware(throttling)

    for mw in (DatabaseMiddleware(), BanCheckMiddleware()):
        dp.message.middleware(mw)
        dp.callback_query.middleware(mw)
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

    background_tasks = {
        asyncio.create_task(scheduler(bot)),
        asyncio.create_task(node_checker_loop()),
    }

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    polling_task = asyncio.create_task(
        dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    )
    logger.info("Bot started")

    await stop_event.wait()
    logger.info("Shutting down")

    polling_task.cancel()
    for task in background_tasks:
        task.cancel()
    await asyncio.gather(polling_task, *background_tasks, return_exceptions=True)

    await runner.cleanup()
    await remnawave.close_client()
    await bot.session.close()
    await dispose_engine()
    logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
