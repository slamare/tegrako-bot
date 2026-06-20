"""Webhook handler for Remnawave panel events."""
import json
import hmac
import hashlib
import logging
from aiohttp import web

from db.database import async_session_maker
from db import dal
from config.settings import settings

logger = logging.getLogger(__name__)

_bot = None


def set_bot(bot):
    """Set bot instance for sending notifications."""
    global _bot
    _bot = bot


def validate_webhook_signature(body: bytes, signature: str, secret: str) -> bool:
    """Validate Remnawave webhook signature using HMAC-SHA256."""
    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def handle_webhook(request: web.Request) -> web.Response:
    """Handle incoming webhook from Remnawave panel."""
    body = await request.read()

    # Validate HMAC signature
    secret = settings.WEBHOOK_SECRET
    if secret:
        signature = request.headers.get("X-Remnawave-Signature", "")
        if not signature:
            logger.warning(f"Webhook: missing signature from {request.remote}")
            return web.Response(status=401, text="Missing signature")

        if not validate_webhook_signature(body, signature, secret):
            logger.warning(f"Webhook: bad secret from {request.remote}")
            return web.Response(status=403, text="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.error("Webhook: invalid JSON")
        return web.Response(status=400, text="Invalid JSON")

    event = payload.get("event", "")
    scope = payload.get("scope", "")
    data = payload.get("data", {})

    logger.info(f"Webhook received: {scope}.{event}")

    if scope == "user":
        await handle_user_event(event, data)

    return web.Response(status=200, text="OK")


async def handle_user_event(event: str, data: dict):
    """Handle user-related webhook events."""
    telegram_id = data.get("telegramId")
    if not telegram_id:
        logger.warning(f"Webhook user event without telegramId: {event}")
        return

    async with async_session_maker() as session:
        user = await dal.get_user(session, telegram_id)
        if not user:
            logger.info(f"Webhook: user {telegram_id} not found in bot DB")
            return

        # Event messages mapping
        messages = {
            "user.expired": (
                "⚠️ <b>Ваша подписка истекла.</b>\n\n"
                "Оформите новую — нажмите «🛒 Купить подписку».",
                "expired",
                None,
            ),
            "user.disabled": (
                "⛔ <b>Ваша подписка отключена.</b>\n\n"
                "Свяжитесь с поддержкой или оформите новую.",
                "disabled",
                None,
            ),
            "user.limited": (
                "📉 <b>Трафик закончился.</b>\n\n"
                "Подписка приостановлена до конца периода. "
                "Докупите трафик или оформите новую.",
                "limited",
                None,
            ),
            "user.expires_in_24_hours": (
                "⏳ <b>Подписка истекает через 24 часа.</b>\n\n"
                "Не забудьте продлить — нажмите «🛒 Купить подписку».",
                "expiring_soon",
                "hours_24",
            ),
            "user.expires_in_48_hours": (
                "⏳ <b>Подписка истекает через 48 часов.</b>\n\n"
                "Не забудьте продлить — нажмите «🛒 Купить подписку».",
                "expiring_soon",
                "hours_48",
            ),
            "user.expires_in_72_hours": (
                "⏳ <b>Подписка истекает через 72 часа.</b>\n\n"
                "Не забудьте продлить — нажмите «🛒 Купить подписку».",
                "expiring_soon",
                "hours_72",
            ),
        }

        msg_data = messages.get(event)
        if not msg_data:
            logger.info(f"Webhook: unhandled event {event} for user {telegram_id}")
            return

        text, notif_type, meta = msg_data

        # Check if already notified
        if await dal.was_notified(session, user.id, notif_type, meta):
            logger.info(f"Webhook: user {telegram_id} already notified for {event}")
            return

        # Send notification
        try:
            if _bot:
                await _bot.send_message(
                    user.telegram_id,
                    text,
                    parse_mode="HTML",
                    disable_notification=True,
                )
                await dal.log_notification(session, user.id, notif_type, meta)
                logger.info(f"Webhook: notified user {telegram_id} about {event}")
        except Exception as e:
            logger.error(f"Webhook: failed to notify user {telegram_id}: {e}")


def create_webhook_app(bot) -> web.Application:
    """Create aiohttp application for webhook server."""
    set_bot(bot)
    app = web.Application()
    app.router.add_post("/webhook", handle_webhook)
    return app