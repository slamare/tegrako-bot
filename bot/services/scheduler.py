import asyncio
import logging
from datetime import datetime, timezone, timedelta

from aiogram import Bot
from db import dal
from bot.services import remnawave
from config.settings import settings

logger = logging.getLogger(__name__)


async def check_expiring_subscriptions(bot: Bot):
    """Один bulk-запрос к панели вместо N запросов по uuid."""
    from db.database import async_session_maker

    panel_users = await remnawave.get_all_users_bulk()
    panel_by_uuid = {u.uuid: u for u in panel_users}

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
                days_left = (rw.expire_at - now).days
                status = rw.status.value

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


async def revoke_expired_mtproto(bot: Bot):
    """Удаляет из telemt пользователей с просроченной > 5 дней подпиской."""
    from sqlalchemy import update as sa_update
    from db.models import User
    from db.database import async_session_maker
    from bot.services import telemt as telemt_svc

    panel_users = await remnawave.get_all_users_bulk()
    panel_by_uuid = {u.uuid: u for u in panel_users}

    async with async_session_maker() as session:
        users = await dal.get_all_users(session, only_registered=True)
        now = datetime.now(timezone.utc)
        grace = timedelta(days=5)

        for user in users:
            if not user.mtproto_secret or not user.remnawave_uuid:
                continue
            rw = panel_by_uuid.get(user.remnawave_uuid)
            if not rw:
                continue
            try:
                if rw.status.value == "EXPIRED" and (now - rw.expire_at) > grace:
                    telemt_svc.remove_user(user.remnawave_username)
                    await session.execute(
                        sa_update(User)
                        .where(User.telegram_id == user.telegram_id)
                        .values(mtproto_secret=None)
                    )
                    await session.commit()
                    logger.info(f"MTProto revoked for expired user {user.remnawave_username}")
                    try:
                        await bot.send_message(
                            user.telegram_id,
                            "📡 <b>MTProto прокси деактивирован.</b>\n\n"
                            "Подписка не оплачена более 5 дней. "
                            "После продления прокси восстановится автоматически.",
                            parse_mode="HTML",
                            disable_notification=True,
                        )
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"MTProto revoke check failed for {user.telegram_id}: {e}")


async def scheduler(bot: Bot):
    """Fallback-проверка каждые 6 часов — страховка если вебхук пропустил событие."""
    await asyncio.sleep(5)
    while True:
        try:
            await check_expiring_subscriptions(bot)
            await revoke_expired_mtproto(bot)
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
        await asyncio.sleep(6 * 3600)
