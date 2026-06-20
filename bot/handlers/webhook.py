"""
Webhook-обработчик событий Remnawave.

Панель шлёт POST /webhook с заголовками:
  X-Remnawave-Signature: HMAC-SHA256(secret, raw_body) в hex
  X-Remnawave-Timestamp: ISO timestamp
"""
import hashlib
import hmac
import logging
from datetime import datetime, timezone, timedelta

from aiohttp import web
from aiogram import Bot

from config.settings import settings
from db.database import async_session_maker
from db import dal

logger = logging.getLogger(__name__)

USER_EXPIRED_EVENTS = {
    "user.expired",
    "user.limited",
    "user.disabled",
}
USER_EXPIRING_EVENTS = {
    "user.expires_in_24_hours": 1,
    "user.expires_in_48_hours": 2,
    "user.expires_in_72_hours": 3,
}


def _verify_signature(secret: str, raw_body: bytes, signature: str) -> bool:
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def handle_webhook(request: web.Request) -> web.Response:
    raw_body = await request.read()

    if settings.WEBHOOK_SECRET:
        signature = request.headers.get("X-Remnawave-Signature", "")
        if not signature:
            logger.warning(f"Webhook: missing signature from {request.remote}")
            return web.Response(status=403, text="Missing signature")
        if not _verify_signature(settings.WEBHOOK_SECRET, raw_body, signature):
            logger.warning(f"Webhook: invalid signature from {request.remote}")
            return web.Response(status=403, text="Invalid signature")

    try:
        payload = json.loads(raw_body)
    except Exception:
        return web.Response(status=400, text="Bad JSON")

    scope = payload.get("scope", "")
    event = payload.get("event", "")
    data  = payload.get("data", {})

    logger.info(f"Webhook: scope={scope} event={event}")

    bot: Bot = request.app["bot"]

    try:
        if scope == "user":
            await _handle_user_event(bot, event, data)
    except Exception as e:
        logger.error(f"Webhook handler error: {e}", exc_info=True)

    return web.Response(status=200)


async def _handle_user_event(bot: Bot, event: str, data: dict):
    tg_id = data.get("telegramId")
    if not tg_id:
        return

    async with async_session_maker() as session:
        user = await dal.get_user(session, tg_id)
        if not user:
            return

        if event in USER_EXPIRED_EVENTS:
            notif_key = f"wh_{event}"
            if not await dal.was_notified(session, user.id, notif_key):
                try:
                    await bot.send_message(
                        tg_id,
                        _expired_text(event),
                        parse_mode="HTML",
                        disable_notification=True,
                    )
                except Exception as e:
                    logger.warning(f"Webhook notify failed for {tg_id}: {e}")
                await dal.log_notification(session, user.id, notif_key)

            if event == "user.expired":
                await _maybe_revoke_mtproto(bot, user, data)

        elif event in USER_EXPIRING_EVENTS:
            days = USER_EXPIRING_EVENTS[event]
            meta = f"wh_days_{days}"
            if not await dal.was_notified(session, user.id, "wh_expiring", meta):
                word = "день" if days == 1 else "дня" if days < 5 else "дней"
                try:
                    await bot.send_message(
                        tg_id,
                        f"⏰ <b>Подписка истекает через {days} {word}!</b>\n\n"
                        f"Продлите — нажмите «🛒 Купить подписку».",
                        parse_mode="HTML",
                        disable_notification=True,
                    )
                except Exception as e:
                    logger.warning(f"Webhook expiring notify failed for {tg_id}: {e}")
                await dal.log_notification(session, user.id, "wh_expiring", meta)


async def _maybe_revoke_mtproto(bot: Bot, user, data: dict):
    mtproto_secret = getattr(user, "mtproto_secret", None)
    if not mtproto_secret:
        return

    expire_str = data.get("expireAt", "")
    if not expire_str:
        return
    try:
        expire_at = datetime.fromisoformat(expire_str.replace("Z", "+00:00"))
    except Exception:
        return

    if (datetime.now(timezone.utc) - expire_at) < timedelta(days=5):
        return

    try:
        from sqlalchemy import update as sa_update
        from db.models import User
        from bot.services import telemt as telemt_svc

        telemt_svc.remove_user(user.remnawave_username)
        async with async_session_maker() as session:
            await session.execute(
                sa_update(User)
                .where(User.telegram_id == user.telegram_id)
                .values(mtproto_secret=None)
            )
            await session.commit()
        await bot.send_message(
            user.telegram_id,
            "📡 <b>MTProto прокси деактивирован.</b>\n\n"
            "Подписка не оплачена более 5 дней. "
            "После продления прокси восстановится автоматически.",
            parse_mode="HTML",
            disable_notification=True,
        )
    except Exception as e:
        logger.warning(f"MTProto revoke via webhook failed for {user.telegram_id}: {e}")


def _expired_text(event: str) -> str:
    if event == "user.limited":
        return (
            "📊 <b>Трафик исчерпан.</b>\n\n"
            "Лимит трафика достигнут. Оформите новую подписку — нажмите «🛒 Купить подписку»."
        )
    if event == "user.disabled":
        return (
            "⛔ <b>Подписка отключена.</b>\n\n"
            "Если считаете ошибкой — обратитесь в поддержку."
        )
    return (
        "⚠️ <b>Ваша подписка истекла.</b>\n\n"
        "Оформите новую — нажмите «🛒 Купить подписку»."
    )


def create_webhook_app(bot: Bot) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app.router.add_post("/webhook", handle_webhook)
    return app
