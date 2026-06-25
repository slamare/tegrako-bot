import logging
from aiogram import Bot
from config.settings import settings

logger = logging.getLogger(__name__)


async def notify_admins(bot: Bot, text: str, parse_mode: str = "HTML") -> None:
    """Рассылает уведомление всем админам из ADMIN_IDS."""
    admin_ids = settings.admin_ids
    if not admin_ids:
        logger.warning("ADMIN_IDS пустые, уведомления не отправлены")
        return

    for admin_id in admin_ids:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode=parse_mode)
            logger.info(f"📩 Уведомление отправлено админу {admin_id}")
        except Exception as e:
            logger.warning(f"❌ Ошибка отправки админу {admin_id}: {e}")
