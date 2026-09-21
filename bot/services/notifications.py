import logging
from typing import Optional
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions
from sqlalchemy.ext.asyncio import AsyncSession
from config.settings import settings
from db import dal

logger = logging.getLogger(__name__)

MENU_BUTTON = InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")


def system_kb(*buttons: InlineKeyboardButton) -> InlineKeyboardMarkup:
    rows = [[b] for b in buttons] + [[MENU_BUTTON]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_system(
    bot: Bot, session: AsyncSession, tg_id: int, text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    silent: bool = True, disable_preview: bool = False,
):
    """Отправляет системное уведомление, предварительно удалив предыдущее.
    У пользователя в чате остаётся не больше одного системного сообщения."""
    key = f"sysmsg:{tg_id}"
    last = await dal.get_setting(session, key, "")
    if last:
        try:
            await bot.delete_message(tg_id, int(last))
        except Exception:
            pass
    msg = await bot.send_message(
        tg_id, text, parse_mode="HTML",
        reply_markup=reply_markup if reply_markup is not None else system_kb(),
        disable_notification=silent,
        link_preview_options=LinkPreviewOptions(is_disabled=True) if disable_preview else None,
    )
    await dal.set_setting(session, key, str(msg.message_id))
    return msg


async def try_send_system(bot: Bot, session: AsyncSession, tg_id: int, text: str, **kwargs) -> bool:
    try:
        await send_system(bot, session, tg_id, text, **kwargs)
        return True
    except Exception as e:
        logger.warning(f"System message to {tg_id} failed: {e}")
        return False


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
