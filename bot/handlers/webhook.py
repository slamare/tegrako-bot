import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone, timedelta

from aiohttp import web
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config.settings import settings
from db import dal
from bot.services.notifications import notify_admins

logger = logging.getLogger(__name__)

USER_EXPIRED_EVENTS = {"user.expired", "user.limited", "user.disabled"}
USER_EXPIRING_EVENTS = {
    "user.expires_in_24_hours": 1,
    "user.expires_in_48_hours": 2,
    "user.expires_in_72_hours": 3,
}

ADMIN_NOTIFY_EVENTS = {
    "user.created": "🆕",
    "user.activated": "✅",
    "user.deactivated": "⛔",
    "user.expired": "⚠️",
    "user.limited": "📊",
    "user.disabled": "🚫",
    "node.connected": "🟢",
    "node.disconnected": "🔴",
    "torrent.blocked": "🚨",
    "user.bandwidth_threshold_60": "📈",
    "user.bandwidth_threshold_80": "📊",
    "user.not_connected_6h": "📱",
    "user.not_connected_24h": "📱",
    "user.not_connected_48h": "📱",
    # системные события панели
    "system.auth": "🔐",
    "panel.started": "🟢",
    "panel.stopped": "🔴",
    "panel.error": "💥",
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
    data = payload.get("data", {})

    logger.info(f"Webhook: scope={scope} event={event} data_keys={list(data.keys())}")

    bot: Bot = request.app["bot"]

    try:
        if scope == "user" or (event == "torrent.blocked" and data.get("telegramId")):
            await _handle_user_event(bot, event, data)

        if event in ADMIN_NOTIFY_EVENTS:
            await _notify_admins_about_event(bot, scope, event, data)
        else:
            # Логируем неизвестные события чтобы понять что шлёт панель
            logger.info(f"Webhook: unhandled event={event} scope={scope}")
    except Exception as e:
        logger.error(f"Webhook handler error: {e}", exc_info=True)

    return web.Response(status=200)


async def _handle_user_event(bot: Bot, event: str, data: dict):
    tg_id = data.get("telegramId")
    if not tg_id:
        return

    from db.database import async_session_maker
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

        elif event == "torrent.blocked":
            await _warn_user_about_torrent(bot, user, data)


async def _warn_user_about_torrent(bot: Bot, user, data: dict):
    try:
        await bot.send_message(
            user.telegram_id,
            "🚨 <b>Обнаружена загрузка торрентов!</b>\n\n"
            "⚠️ Загрузка торрентов запрещена правилами сервиса.\n"
            "При повторных нарушениях подписка может быть заблокирована.\n\n"
            "Пожалуйста, используйте сервис только для легального контента.",
            parse_mode="HTML",
        )
        logger.info(f"Torrent warning sent to {user.telegram_id}")
    except Exception as e:
        logger.warning(f"Failed to send torrent warning to {user.telegram_id}: {e}")


async def _notify_admins_about_event(bot: Bot, scope: str, event: str, data: dict):
    emoji = ADMIN_NOTIFY_EVENTS.get(event, "📢")
    title = _get_event_title(event)
    details = _format_event_details(scope, event, data)
    text = f"{emoji} <b>{title}</b>\n\n{details}"

    keyboard = None
    if event == "torrent.blocked":
        tg_id = data.get("telegramId")
        if tg_id:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👤 Профиль", callback_data=f"admin_user:{tg_id}")]
            ])

    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.warning(f"Admin notify failed for {admin_id}: {e}")


def _get_event_title(event: str) -> str:
    titles = {
        "user.created": "Новый пользователь",
        "user.activated": "Пользователь активирован",
        "user.deactivated": "Пользователь деактивирован",
        "user.expired": "Подписка истекла",
        "user.limited": "Лимит трафика достигнут",
        "user.disabled": "Подписка отключена",
        "user.expires_in_24_hours": "Подписка истекает через 1 день",
        "user.expires_in_48_hours": "Подписка истекает через 2 дня",
        "user.expires_in_72_hours": "Подписка истекает через 3 дня",
        "node.connected": "Нода подключена",
        "node.disconnected": "Нода отключена",
        "torrent.blocked": "Торрент заблокирован",
        "user.bandwidth_threshold_60": "Использовано 60% трафика",
        "user.bandwidth_threshold_80": "Использовано 80% трафика",
        "user.not_connected_6h": "Не подключался 6 часов",
        "user.not_connected_24h": "Не подключался 24 часа",
        "user.not_connected_48h": "Не подключался 48 часов",
        "system.auth": "Вход в панель",
        "panel.started": "Панель запущена",
        "panel.stopped": "Панель остановлена",
        "panel.error": "Ошибка панели",
    }
    return titles.get(event, event)


def _format_event_details(scope: str, event: str, data: dict) -> str:
    lines = []

    username = data.get("username") or data.get("shortUuid") or "—"
    tg_id = data.get("telegramId")

    if scope == "user" or tg_id:
        lines.append(f"👤 <code>{username}</code>")
        if tg_id:
            lines.append(f"🆔 <code>{tg_id}</code>")

    if "expireAt" in data:
        try:
            expire_at = datetime.fromisoformat(data["expireAt"].replace("Z", "+00:00"))
            lines.append(f"📅 Истекает: {expire_at.strftime('%d.%m.%Y %H:%M')}")
        except Exception:
            pass

    if "status" in data:
        lines.append(f"📊 Статус: {data['status']}")

    if "trafficUsedBytes" in data:
        lines.append(f"📈 Трафик: {data['trafficUsedBytes'] / 1024**3:.2f} ГБ")

    if "lifetimeTrafficUsedBytes" in data:
        lines.append(f"📊 Всего: {data['lifetimeTrafficUsedBytes'] / 1024**3:.2f} ГБ")

    if scope == "node":
        node_name = data.get("name") or data.get("nodeName") or "—"
        lines.append(f"🖥 Нода: <code>{node_name}</code>")
        if "address" in data:
            lines.append(f"🌐 {data['address']}")

    if event == "system.auth":
        if "ip" in data:
            lines.append(f"🌐 IP: <code>{data['ip']}</code>")
        if "userAgent" in data:
            lines.append(f"🖥 UA: {data['userAgent'][:60]}")
        if "login" in data:
            lines.append(f"👤 Логин: <code>{data['login']}</code>")

    if event == "torrent.blocked":
        if "torrentHash" in data:
            lines.append(f"🔗 Hash: <code>{data['torrentHash'][:16]}...</code>")
        if "fileName" in data:
            lines.append(f"📁 {data['fileName']}")

    if event in ("panel.error",):
        if "message" in data:
            lines.append(f"💬 {data['message'][:200]}")

    return "\n".join(lines) if lines else "Нет данных"


async def _maybe_revoke_mtproto(bot: Bot, user, data: dict):
    if not getattr(user, "mtproto_secret", None):
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
        from db.database import async_session_maker

        await telemt_svc.remove_user(user.remnawave_username)
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
