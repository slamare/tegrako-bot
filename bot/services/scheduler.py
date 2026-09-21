import asyncio
import logging
from datetime import datetime, timezone, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton
from db import dal
from bot.services import remnawave
from bot.services.notifications import send_system, system_kb
from config.settings import settings

logger = logging.getLogger(__name__)


EXPIRED_WINDOW = timedelta(days=2)

RENEW_KB = system_kb(InlineKeyboardButton(text="🔄 Продлить подписку", callback_data="renew_subscription"))


async def _push(bot: Bot, session, user, kind: str, meta: str, text: str, kb=None):
    try:
        await send_system(bot, session, user.telegram_id, text, kb)
    except TelegramForbiddenError:
        pass
    await dal.log_notification(session, user.id, kind, meta)


async def check_expiring_subscriptions(bot: Bot, panel_by_uuid: dict):
    from db.database import async_session_maker

    notify_days = sorted(settings.notify_expiry_days)
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
                if status not in ("ACTIVE", "EXPIRED"):
                    continue

                remaining = rw.expire_at - now
                period = rw.expire_at.strftime("%Y-%m-%d")
                date_str = rw.expire_at.strftime("%d.%m.%Y")

                if remaining <= timedelta(0):
                    if remaining < -EXPIRED_WINDOW:
                        continue
                    if await dal.was_notified(session, user.id, "expired", period, within_days=None):
                        continue
                    await _push(
                        bot, session, user, "expired", period,
                        f"Подписка закончилась {date_str}. Новую можно оформить кнопкой ниже.",
                        RENEW_KB,
                    )
                    continue

                if status != "ACTIVE":
                    continue
                threshold = next((d for d in notify_days if remaining <= timedelta(days=d)), None)
                if threshold is None:
                    continue
                meta = f"{threshold}:{period}"
                if await dal.was_notified(session, user.id, "expiring_soon", meta, within_days=None):
                    continue
                await _push(
                    bot, session, user, "expiring_soon", meta,
                    f"Подписка действует до {date_str}. Продлить можно кнопкой ниже.",
                    RENEW_KB,
                )
                await asyncio.sleep(0.05)

            except Exception as e:
                logger.warning(f"Notification check failed for {user.telegram_id}: {e}")


async def revoke_expired_mtproto(bot: Bot, panel_by_uuid: dict):
    """Комментирует в telemt тех, у кого подписка истекла более 5 дней назад.

    Секрет НЕ сбрасывается в БД — при продлении пользователь восстановится автоматически.
    Уведомление отправляется один раз за период подписки.
    """
    from db.database import async_session_maker
    from bot.services import telemt as telemt_svc

    grace = timedelta(days=5)
    now = datetime.now(timezone.utc)

    async with async_session_maker() as session:
        users = await dal.get_all_users(session, only_registered=True)
        for user in users:
            if not (user.mtproto_secret and user.remnawave_uuid):
                continue
            rw = panel_by_uuid.get(user.remnawave_uuid)
            if not rw or rw.status.value != "EXPIRED" or (now - rw.expire_at) <= grace:
                continue
            period = rw.expire_at.strftime("%Y-%m-%d")
            try:
                if await dal.was_notified(session, user.id, "mtproto_off", period, within_days=None):
                    continue
                try:
                    await telemt_svc.comment_user(user.remnawave_username)
                except Exception as e:
                    logger.warning(f"telemt comment failed for {user.remnawave_username}: {e}")
                logger.info(f"MTProto commented for {user.remnawave_username}")
                try:
                    await send_system(
                        bot, session, user.telegram_id,
                        "📡 <b>MTProto прокси отключён</b>\n\n"
                        "Подписка не продлевалась более 5 дней. После продления прокси включится снова.",
                    )
                except TelegramForbiddenError:
                    pass
                await dal.log_notification(session, user.id, "mtproto_off", period)
            except Exception as e:
                logger.warning(f"MTProto revoke failed for {user.telegram_id}: {e}")


async def sync_mtproto_users(bot: Bot, panel_by_uuid: dict):
    """Синхронизирует MTProto пользователей: создаёт в telemt тех, у кого активная подписка но нет mtproto_secret."""
    from db.database import async_session_maker
    from bot.services import telemt as telemt_svc
    import secrets

    async with async_session_maker() as session:
        users = await dal.get_all_users(session, only_registered=True)
        to_sync = [
            u for u in users
            if not u.mtproto_secret and u.remnawave_uuid and u.remnawave_username
            and (rw := panel_by_uuid.get(u.remnawave_uuid))
            and rw.status.value == "ACTIVE"
        ]

        if not to_sync:
            logger.info("MTProto sync: all users with active subscriptions have secrets")
            return

        logger.info(f"MTProto sync: found {len(to_sync)} users without secrets")

        for user in to_sync:
            try:
                secret = telemt_svc.generate_secret()
                await telemt_svc.add_user(user.remnawave_username, secret, max_ips=5)
                await dal.update_user(session, user.telegram_id, mtproto_secret=secret)
                logger.info(f"MTProto sync: created for {user.remnawave_username}")
            except Exception as e:
                logger.warning(f"MTProto sync failed for {user.remnawave_username}: {e}")

        await session.commit()


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