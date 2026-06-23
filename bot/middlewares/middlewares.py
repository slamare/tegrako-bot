from typing import Callable, Any
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
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
