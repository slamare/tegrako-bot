import time
from typing import Callable, Any
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from config.settings import settings
from db import dal


class DatabaseMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable, event: TelegramObject, data: dict) -> Any:
        from db.database import async_session_maker
        async with async_session_maker() as session:
            data["session"] = session
            return await handler(event, data)


class BanCheckMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable, event: TelegramObject, data: dict) -> Any:
        session = data.get("session")
        if not session:
            return await handler(event, data)

        tg_user = getattr(event, "from_user", None)
        if not tg_user:
            return await handler(event, data)

        db_user = await dal.get_user(session, tg_user.id)
        if db_user and db_user.is_banned:
            if isinstance(event, Message):
                await event.answer("🚫 Ваш аккаунт заблокирован. Обратитесь в поддержку.")
            elif isinstance(event, CallbackQuery):
                await event.answer("🚫 Ваш аккаунт заблокирован.", show_alert=True)
            return

        return await handler(event, data)


class ThrottlingMiddleware(BaseMiddleware):
    """Простой троттлинг: не чаще одного апдейта на interval секунд на пользователя.

    Callback-запросы всегда тихо подтверждаются (answer), чтобы у клиента не крутились часы.
    """

    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self._last_seen: dict[int, float] = {}

    async def __call__(self, handler: Callable, event: TelegramObject, data: dict) -> Any:
        tg_user = getattr(event, "from_user", None)
        if not tg_user or tg_user.id in settings.admin_ids:
            return await handler(event, data)

        now = time.monotonic()
        last = self._last_seen.get(tg_user.id, 0.0)
        if now - last < self.interval:
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer()
                except Exception:
                    pass
            return
        self._last_seen[tg_user.id] = now

        return await handler(event, data)
