"""Webhook handler for Remnawave panel events."""
import json
import hmac
import hashlib
import logging
from aiohttp import web

from bot.services.subscription import SubscriptionService
from bot.services.user import UserService
from bot.services.notifications import NotificationService
from bot.database.models import User
from bot.database.database import async_session
from config.settings import settings

logger = logging.getLogger(__name__)


def validate_webhook_signature(body: bytes, signature: str, secret: str) -> bool:
    """Validate Remnawave webhook signature using HMAC-SHA256."""
    expected = hmac.new(
        secret.encode('utf-8'),
        body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def handle_webhook(request: web.Request) -> web.Response:
    """Handle incoming webhook from Remnawave panel."""
    body = await request.read()
    
    # Validate signature
    if settings.WEBHOOK_SECRET:
        signature = request.headers.get("X-Remnawave-Signature", "")
        if not signature:
            logger.warning(f"Webhook: missing signature from {request.remote}")
            return web.Response(status=401, text="Missing signature")
        
        if not validate_webhook_signature(body, signature, settings.WEBHOOK_SECRET):
            logger.warning(f"Webhook: invalid signature from {request.remote}")
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
    
    # Handle user events
    if scope == "user":
        await handle_user_event(event, data)
    
    return web.Response(status=200, text="OK")


async def handle_user_event(event: str, data: dict):
    """Handle user-related webhook events."""
    telegram_id = data.get("telegramId")
    if not telegram_id:
        logger.warning(f"Webhook user event without telegramId: {event}")
        return
    
    async with async_session() as session:
        user = await session.get(User, telegram_id)
        if not user:
            logger.info(f"Webhook: user {telegram_id} not found in bot DB")
            return
        
        if event == "user.expired":
            logger.info(f"Webhook: user {telegram_id} subscription expired")
            user.subscription_status = "expired"
            await session.commit()
            await NotificationService.notify_subscription_expired(user)
        
        elif event == "user.disabled":
            logger.info(f"Webhook: user {telegram_id} disabled")
            user.subscription_status = "disabled"
            await session.commit()
            await NotificationService.notify_subscription_disabled(user)
        
        elif event == "user.limited":
            logger.info(f"Webhook: user {telegram_id} traffic limited")
            user.subscription_status = "limited"
            await session.commit()
            await NotificationService.notify_traffic_limited(user)
        
        elif event == "user.expires_in_24_hours":
            logger.info(f"Webhook: user {telegram_id} expires in 24h")
            await NotificationService.notify_expiring_soon(user, hours=24)
        
        elif event == "user.expires_in_48_hours":
            logger.info(f"Webhook: user {telegram_id} expires in 48h")
            await NotificationService.notify_expiring_soon(user, hours=48)
        
        elif event == "user.expires_in_72_hours":
            logger.info(f"Webhook: user {telegram_id} expires in 72h")
            await NotificationService.notify_expiring_soon(user, hours=72)


def setup_webhook_routes(app: web.Application):
    """Setup webhook routes."""
    app.router.add_post("/webhook", handle_webhook)
