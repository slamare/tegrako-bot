import logging
from typing import Optional
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup
from config.settings import settings

logger = logging.getLogger(__name__)


async def notify_admins(
    bot: Bot, text: str, parse_mode: str = "HTML",
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    disable_notification: bool = False,
) -> None:
    """Рассылает уведомление всем админам из ADMIN_IDS. Ошибки отдельных доставок логируются, не глушатся."""
    admin_ids = settings.admin_ids
    if not admin_ids:
        logger.warning("ADMIN_IDS пустые, уведомления не отправлены")
        return

    for admin_id in admin_ids:
        try:
            await bot.send_message(
                chat_id=admin_id, text=text, parse_mode=parse_mode,
                reply_markup=reply_markup, disable_notification=disable_notification,
            )
        except Exception as e:
            logger.warning(f"Admin notify failed for {admin_id}: {e}")
