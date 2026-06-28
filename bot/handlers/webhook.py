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

logger = logging.getLogger(__name__)

USER_EXPIRED_EVENTS = {"user.expired", "user.limited", "user.disabled"}
USER_EXPIRING_EVENTS = {
    "user.expires_in_24_hours": 1,
    "user.expires_in_48_hours": 2,
    "user.expires_in_72_hours": 3,
}

ADMIN_NOTIFY_EVENTS = {
    # user scope
    "user.created": "🆕",
    "user.expired": "⚠️",
    "user.limited": "📊",
    "user.disabled": "🚫",
    "user.enabled": "✅",
    "user.bandwidth_usage_threshold_reached": "📈",
    # node scope
    "node.connection_lost": "🔴",
    "node.connection_restored": "🟢",
    # service scope
    "service.panel_started": "🚀",
    "service.login_attempt_success": "🔐",
    "service.login_attempt_failed": "🚨",
    # torrent_blocker scope
    "torrent_blocker.report": "🏴‍☠️",
    # crm scope
    "crm.infra_billing_node_payment_due_today": "💳",
    "crm.infra_billing_node_payment_overdue_24hrs": "❗",
    "crm.infra_billing_node_payment_overdue_48hrs": "❗",
    "crm.infra_billing_node_payment_overdue_7_days": "🆘",
}


def _verify_signature(secret: str, body: dict, signature: str) -> bool:
    # Панель подписывает json.dumps(body) с separators=(',', ':')
    body_str = json.dumps(body, separators=(",", ":"))
    expected = hmac.new(secret.encode(), body_str.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def handle_webhook(request: web.Request) -> web.Response:
    raw_body = await request.read()

    try:
        payload = json.loads(raw_body)
    except Exception:
        return web.Response(status=400, text="Bad JSON")

    if settings.WEBHOOK_SECRET:
        signature = request.headers.get("X-Remnawave-Signature", "")
        if not signature:
            logger.warning(f"Webhook: missing signature from {request.remote}")
            return web.Response(status=403, text="Missing signature")
        if not _verify_signature(settings.WEBHOOK_SECRET, payload, signature):
            logger.warning(f"Webhook: invalid signature from {request.remote}")
            return web.Response(status=403, text="Invalid signature")

    scope = payload.get("scope", "")
    event = payload.get("event", "")
    data = payload.get("data", {})

    logger.info(f"Webhook: scope={scope} event={event}")

    bot: Bot = request.app["bot"]

    try:
        if scope == "user":
            await _handle_user_event(bot, event, data)
        elif scope == "user_hwid_devices":
            await _handle_hwid_event(bot, event, data)
        elif scope == "torrent_blocker":
            await _handle_torrent_blocker(bot, event, data)

        if event in ADMIN_NOTIFY_EVENTS:
            await _notify_admins(bot, scope, event, data)
        else:
            logger.debug(f"Webhook: unhandled event={event} scope={scope}")
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
                    await bot.send_message(tg_id, _expired_text(event), parse_mode="HTML", disable_notification=True)
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
                        f"⏰ <b>Подписка истекает через {days} {word}!</b>\n\nПродлите — нажмите «🛒 Купить подписку».",
                        parse_mode="HTML",
                        disable_notification=True,
                    )
                except Exception as e:
                    logger.warning(f"Webhook expiring notify failed for {tg_id}: {e}")
                await dal.log_notification(session, user.id, "wh_expiring", meta)

        elif event == "user.expired_24_hours_ago":
            if not await dal.was_notified(session, user.id, "wh_expired_24h"):
                try:
                    await bot.send_message(
                        tg_id,
                        "😔 <b>Подписка истекла вчера.</b>\n\n"
                        "Не теряйте доступ надолго — оформите новую подписку прямо сейчас.\n"
                        "Нажмите «🛒 Купить подписку».",
                        parse_mode="HTML",
                        disable_notification=True,
                    )
                except Exception as e:
                    logger.warning(f"Webhook expired_24h notify failed for {tg_id}: {e}")
                await dal.log_notification(session, user.id, "wh_expired_24h")

        elif event == "user.not_connected":
            hours_list = data.get("notConnectedHours") or []
            hours = hours_list[0] if hours_list else None
            meta = f"wh_nc_{hours}" if hours else "wh_nc"
            if not await dal.was_notified(session, user.id, "wh_not_connected", meta):
                hours_str = f" {hours} часов" if hours else ""
                try:
                    await bot.send_message(
                        tg_id,
                        f"📱 <b>Вы ещё не подключились к VPN!</b>\n\n"
                        f"Подписка активна, но соединение не установлено{hours_str}.\n"
                        f"Перейдите в «👤 Личный кабинет» → «Моя подписка» чтобы получить ссылку подключения.",
                        parse_mode="HTML",
                        disable_notification=True,
                    )
                except Exception as e:
                    logger.warning(f"Webhook not_connected notify failed for {tg_id}: {e}")
                await dal.log_notification(session, user.id, "wh_not_connected", meta)


async def _handle_torrent_blocker(bot: Bot, event: str, data: dict):
    if event != "torrent_blocker.report":
        return
    user_data = data.get("userData", {})
    tg_id = user_data.get("telegramId")
    if not tg_id:
        return
    from db.database import async_session_maker
    async with async_session_maker() as session:
        user = await dal.get_user(session, tg_id)
        if not user:
            return
    try:
        await bot.send_message(
            tg_id,
            "🏴‍☠️ <b>Обнаружена загрузка торрентов!</b>\n\n"
            "Загрузка торрентов запрещена правилами сервиса.\n"
            "При повторных нарушениях подписка будет заблокирована.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Torrent warning failed for {tg_id}: {e}")


async def _handle_hwid_event(bot: Bot, event: str, data: dict):
    user_data = data.get("user", {}) or data
    tg_id = user_data.get("telegramId")
    if not tg_id:
        return
    from db.database import async_session_maker
    async with async_session_maker() as session:
        user = await dal.get_user(session, tg_id)
        if not user:
            return
    if event == "user_hwid_devices.added":
        platform = data.get("platform") or data.get("device", {}).get("platform") or "новое устройство"
        model = data.get("deviceModel") or data.get("device", {}).get("deviceModel") or ""
        device_str = f"{platform} {model}".strip()
        kb_rows = [
            [InlineKeyboardButton(text="🔄 Сбросить ссылку подписки", callback_data="revoke_subscription_confirm")],
        ]
        try:
            await bot.send_message(
                tg_id,
                f"📱 <b>Новое устройство подключено</b>\n\n"
                f"К вашему аккаунту добавлено: <b>{device_str}</b>\n\n"
                f"Если это не вы — возможно ваша ссылка подписки скомпрометирована. "
                f"Сбросьте её кнопкой ниже.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
                disable_notification=True,
            )
        except Exception as e:
            logger.warning(f"HWID added notify failed for {tg_id}: {e}")


async def _notify_admins(bot: Bot, scope: str, event: str, data: dict):
    emoji = ADMIN_NOTIFY_EVENTS.get(event, "📢")
    title = _get_title(event)
    details = _format_details(scope, event, data)
    text = f"{emoji} <b>{title}</b>\n\n{details}"

    rows = []
    if event == "torrent_blocker.report":
        user_data = data.get("userData", {})
        tg_id = user_data.get("telegramId")
        if tg_id:
            rows.append([
                InlineKeyboardButton(text="👤 Профиль", callback_data=f"admin_user:{tg_id}"),
                InlineKeyboardButton(text="🚫 Забанить", callback_data=f"toggle_ban:{tg_id}"),
            ])
            rows.append([
                InlineKeyboardButton(text="✉️ Написать", callback_data=f"torrent_warn_user:{tg_id}"),
            ])
    rows.append([InlineKeyboardButton(text="✅ Прочитано", callback_data="notify_dismiss")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)

    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML", reply_markup=keyboard)
        except Exception as e:
            logger.warning(f"Admin notify failed for {admin_id}: {e}")


def _get_title(event: str) -> str:
    return {
        "user.created": "Новый пользователь",
        "user.expired": "Подписка истекла",
        "user.limited": "Лимит трафика достигнут",
        "user.disabled": "Подписка отключена",
        "user.enabled": "Подписка активирована",
        "user.bandwidth_usage_threshold_reached": "Порог трафика достигнут",
        "user.not_connected": "Пользователь не подключался",
        "node.connection_lost": "Нода недоступна",
        "node.connection_restored": "Нода восстановлена",
        "service.panel_started": "Панель запущена",
        "service.login_attempt_success": "Вход в панель",
        "service.login_attempt_failed": "Неудачная попытка входа",
        "torrent_blocker.report": "Торрент заблокирован",
        "crm.infra_billing_node_payment_due_today": "Оплата ноды — сегодня",
        "crm.infra_billing_node_payment_overdue_24hrs": "Оплата ноды просрочена 24ч",
        "crm.infra_billing_node_payment_overdue_48hrs": "Оплата ноды просрочена 48ч",
        "crm.infra_billing_node_payment_overdue_7_days": "Оплата ноды просрочена 7 дней",
    }.get(event, event)


def _format_details(scope: str, event: str, data: dict) -> str:
    lines = []

    if scope == "user":
        username = data.get("username") or "—"
        tg_id = data.get("telegramId")
        lines.append(f"👤 <code>{username}</code>")
        if tg_id:
            lines.append(f"🆔 <code>{tg_id}</code>")
        if "expireAt" in data:
            try:
                dt = datetime.fromisoformat(data["expireAt"].replace("Z", "+00:00"))
                lines.append(f"📅 {dt.strftime('%d.%m.%Y %H:%M')}")
            except Exception:
                pass
        if "status" in data:
            lines.append(f"📊 {data['status']}")
        if "usedTrafficBytes" in data:
            lines.append(f"📈 Трафик: {int(data['usedTrafficBytes']) / 1024**3:.2f} ГБ")

    elif scope == "node":
        lines.append(f"🖥 <code>{data.get('name') or '—'}</code>")
        if "address" in data:
            lines.append(f"🌐 {data['address']}")

    elif scope == "service":
        if "ip" in data:
            lines.append(f"🌐 IP: <code>{data['ip']}</code>")
        if "login" in data:
            lines.append(f"👤 Логин: <code>{data['login']}</code>")
        if "userAgent" in data:
            lines.append(f"🖥 {data['userAgent'][:80]}")
        if "panelVersion" in data:
            lines.append(f"🔖 v{data['panelVersion']}")

    elif scope == "torrent_blocker":
        user_data = data.get("userData", {})
        action = data.get("actionReport", {})
        xray = data.get("xrayReport", {})
        node = data.get("nodeData", {})
        if user_data.get("username"):
            lines.append(f"👤 <code>{user_data['username']}</code>")
        if user_data.get("telegramId"):
            lines.append(f"🆔 <code>{user_data['telegramId']}</code>")
        if node.get("name"):
            lines.append(f"🖥 Нода: {node['name']}")
        if action.get("blockedIp"):
            lines.append(f"🌐 IP: <code>{action['blockedIp']}</code>")
        if xray.get("destination"):
            lines.append(f"🎯 Dest: {xray['destination']}")

    elif scope == "crm":
        if "nodeName" in data:
            lines.append(f"🖥 Нода: <code>{data['nodeName']}</code>")
        if "amount" in data:
            lines.append(f"💵 {data['amount']}")

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
                sa_update(User).where(User.telegram_id == user.telegram_id).values(mtproto_secret=None)
            )
            await session.commit()
        await bot.send_message(
            user.telegram_id,
            "📡 <b>MTProto прокси деактивирован.</b>\n\nПодписка не оплачена более 5 дней. "
            "После продления прокси восстановится автоматически.",
            parse_mode="HTML",
            disable_notification=True,
        )
    except Exception as e:
        logger.warning(f"MTProto revoke via webhook failed for {user.telegram_id}: {e}")


def _expired_text(event: str) -> str:
    if event == "user.limited":
        return "📊 <b>Трафик исчерпан.</b>\n\nЛимит трафика достигнут. Оформите новую подписку — нажмите «🛒 Купить подписку»."
    if event == "user.disabled":
        return "⛔ <b>Подписка отключена.</b>\n\nЕсли считаете ошибкой — обратитесь в поддержку."
    return "⚠️ <b>Ваша подписка истекла.</b>\n\nОформите новую — нажмите «🛒 Купить подписку»."


def create_webhook_app(bot: Bot) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app.router.add_post("/webhook", handle_webhook)
    return app
