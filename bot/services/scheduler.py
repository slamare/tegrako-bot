import asyncio
import logging
from datetime import datetime, timezone, timedelta

from aiogram import Bot
from db import dal
from bot.services import remnawave
from config.settings import settings

logger = logging.getLogger(__name__)


async def check_expiring_subscriptions(bot: Bot, panel_by_uuid: dict):
    from db.database import async_session_maker

    notify_days = settings.notify_expiry_days
    now = datetime.now(timezone.utc)

    async with async_session_maker() as session:
        users = await dal.get_all_users(session, only_registered=True)
        for user in users:
            if not user.remnawave_uuid:
                continue
            rw = panel_by_uuid.get(user.remnawave_uuid)
            if not rw:
                continue
            try:
                status = rw.status.value
                days_left = (rw.expire_at - now).days

                if status == "EXPIRED":
                    if not await dal.was_notified(session, user.id, "expired"):
                        await bot.send_message(
                            user.telegram_id,
                            "⚠️ <b>Ваша подписка истекла.</b>\n\nОформите новую — нажмите «🛒 Купить подписку».",
                            parse_mode="HTML",
                            disable_notification=True,
                        )
                        await dal.log_notification(session, user.id, "expired")

                elif status == "ACTIVE":
                    for d in notify_days:
                        if days_left == d:
                            meta = f"days_{d}"
                            if not await dal.was_notified(session, user.id, "expiring_soon", meta):
                                word = "день" if d == 1 else "дня" if d < 5 else "дней"
                                await bot.send_message(
                                    user.telegram_id,
                                    f"⏰ <b>Подписка истекает через {d} {word}!</b>\n\n"
                                    f"Продлите — нажмите «🛒 Купить подписку».",
                                    parse_mode="HTML",
                                    disable_notification=True,
                                )
                                await dal.log_notification(session, user.id, "expiring_soon", meta)

            except Exception as e:
                logger.warning(f"Notification check failed for {user.telegram_id}: {e}")


async def revoke_expired_mtproto(bot: Bot, panel_by_uuid: dict):
    """Комментирует в telemt тех, у кого подписка истекла более 5 дней назад.
    
    Секрет НЕ сбрасывается в БД — при продлении пользователь восстановится автоматически.
    """
    from db.database import async_session_maker
    from bot.services import telemt as telemt_svc

    grace = timedelta(days=5)
    now = datetime.now(timezone.utc)

    async with async_session_maker() as session:
        users = await dal.get_all_users(session, only_registered=True)
        to_revoke = [
            u for u in users
            if u.mtproto_secret and u.remnawave_uuid
            and (rw := panel_by_uuid.get(u.remnawave_uuid))
            and rw.status.value == "EXPIRED"
            and (now - rw.expire_at) > grace
        ]

        if not to_revoke:
            return

        for user in to_revoke:
            try:
                # Комментируем вместо удаления — секрет сохраняется в конфиге
                telemt_svc.comment_user(user.remnawave_username)
            except Exception as e:
                logger.warning(f"telemt comment failed for {user.remnawave_username}: {e}")

        for user in to_revoke:
            logger.info(f"MTProto commented for {user.remnawave_username}")
            try:
                await bot.send_message(
                    user.telegram_id,
                    "📡 <b>MTProto прокси деактивирован.</b>\n\n"
                    "Подписка не оплачена более 5 дней. После продления прокси восстановится автоматически.",
                    parse_mode="HTML",
                    disable_notification=True,
                )
            except Exception:
                pass


async def scheduler(bot: Bot):
    await asyncio.sleep(5)
    while True:
        try:
            panel_users = await remnawave.get_all_users_bulk()
            panel_by_uuid = {u.uuid: u for u in panel_users}
            logger.info(f"Scheduler: loaded {len(panel_by_uuid)} users from panel")
            await check_expiring_subscriptions(bot, panel_by_uuid)
            await revoke_expired_mtproto(bot, panel_by_uuid)
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
        await asyncio.sleep(6 * 3600)
