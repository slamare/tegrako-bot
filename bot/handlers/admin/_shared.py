from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from db import dal

logger = logging.getLogger(__name__)


def admin_nav_kb(back_callback: str = "admin_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=back_callback)],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])


async def _provision_mtproto(session: AsyncSession, user, tariff) -> None:
    """Выдаёт или обновляет MTProto-секрет пользователя в telemt."""
    try:
        from bot.services import telemt as telemt_svc
        from bot.services import remnawave

        rw = await remnawave.get_subscription_info(user.remnawave_uuid) if user.remnawave_uuid else None
        hwid_limit = rw.hwid_device_limit if rw else 0

        max_ips = max(1, hwid_limit) if hwid_limit else 5
        if not user.mtproto_secret:
            secret = telemt_svc.generate_secret()
            await telemt_svc.add_user(user.remnawave_username, secret, max_ips=max_ips)
            await dal.update_user(session, user.telegram_id, mtproto_secret=secret)
        else:
            await telemt_svc.add_user(user.remnawave_username, user.mtproto_secret, max_ips=max_ips)
    except Exception as e:
        logger.warning(f"MTProto provision failed for {user.telegram_id}: {e}")
