import logging
logger = logging.getLogger(__name__)
from aiogram import Router, F
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    ReplyKeyboardRemove, CopyTextButton,
)
from sqlalchemy.ext.asyncio import AsyncSession
from cachetools import TTLCache
import re
import asyncio
from datetime import datetime, timezone

from bot.states.states import RegistrationSG, SupportSG
from bot.services.notifications import notify_admins
from bot.keyboards.user_kb import (
    main_menu_kb, back_kb, proxy_kb,
    devices_kb, cancel_kb, remove_kb, _device_icon,
)
from bot.services import remnawave
from bot.utils.helpers import (
    edit_or_answer, show_menu_message, menu_cache,
    cleanup_fsm_interaction, delete_later,
)
from config.settings import settings
from db import dal

router = Router()

# Кэш для предотвращения спама уведомлениями (TTL 30 сек)
_notification_cache = TTLCache(maxsize=1000, ttl=30)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _has_active_proxy_access(rw) -> bool:
    """Прокси доступен при ACTIVE или в течение 5 дней после истечения."""
    if not rw:
        return False
    status = rw.status.value
    if status == "ACTIVE":
        return True
    if status == "EXPIRED":
        days_since_expired = (datetime.now(timezone.utc) - rw.expire_at).days
        return days_since_expired < 5
    return False


LIFETIME_DAYS_THRESHOLD = 3650


def _days_left_label(days_left: int) -> str:
    if days_left > LIFETIME_DAYS_THRESHOLD:
        return "♾ Бессрочно"
    return f"{days_left} дн."


def _sub_status_line(rw, device_count: int = 0, device_limit: int = 0) -> str:
    if not rw:
        return "\n\n🔴 Подписка не активна"
    now = datetime.now(timezone.utc)
    days_left = (rw.expire_at - now).days
    if rw.status.value == "ACTIVE":
        used_gb = round(rw.user_traffic.used_traffic_bytes / 1024 ** 3, 1)
        limit_gb = round(rw.traffic_limit_bytes / 1024 ** 3, 1) if rw.traffic_limit_bytes else None
        traffic = f"{used_gb}/{limit_gb} ГБ" if limit_gb else f"{used_gb} ГБ"
        limit_str = str(device_limit) if device_limit else "∞"
        return (
            f"\n\n🟢 Подписка активна · {_days_left_label(days_left)}"
            f"\n📊 Трафик: {traffic}"
            f"\n📱 Устройства: {device_count} / {limit_str}"
        )
    if rw.status.value == "EXPIRED":
        return "\n\n🔴 Подписка истекла"
    return f"\n\n⚪ Статус подписки: {rw.status.value}"


async def _get_menu_context(session, tg_id: int, remnawave_uuid: str | None) -> tuple[InlineKeyboardMarkup, str]:
    is_adm = tg_id in settings.admin_ids
    show_proxy = False
    sub_url = None
    status = "NONE"
    is_lifetime = False
    status_line = "\n\n🔴 Подписка не активна"
    if remnawave_uuid:
        try:
            rw = await remnawave.get_subscription_info(remnawave_uuid)
            devices = await remnawave.get_user_devices(remnawave_uuid)
            status_line = _sub_status_line(rw, len(devices), rw.hwid_device_limit if rw else 0)
            if rw:
                status = rw.status.value if rw.status.value in ("ACTIVE", "EXPIRED") else "NONE"
                if status == "ACTIVE":
                    sub_url = rw.subscription_url
                    now = datetime.now(timezone.utc)
                    is_lifetime = (rw.expire_at - now).days > LIFETIME_DAYS_THRESHOLD
            user = await dal.get_user(session, tg_id)
            has_secret = bool(user and user.mtproto_secret)
            if has_secret:
                show_proxy = _has_active_proxy_access(rw)
        except Exception as e:
            logger.warning(f"menu context failed tg={tg_id}: {e}")
    kb = main_menu_kb(is_admin=is_adm, status=status, show_proxy=show_proxy, sub_url=sub_url, is_lifetime=is_lifetime)
    return kb, status_line


def _welcome_text(status_line: str = "") -> str:
    return (
        f"👋 Главное меню\n\n"
        f"<b>{settings.BOT_NAME}</b> - Сервис для защиты соединения и обеспечения приватности в сети."
        f"{status_line}\n\n"
        f"Выберите действие в меню ниже."
    )


async def _check_access(session, tg_id: int, action: str) -> tuple[bool, str]:
    if tg_id in settings.admin_ids:
        return True, ""
    mode = await dal.get_setting(session, "access_mode", "open")
    if mode == "closed":
        return False, "🔧 Сервис временно недоступен. Попробуйте позже."
    if mode == "invite_only" and action == "register":
        return False, "🔒 Регистрация доступна только по реферальной ссылке."
    if mode == "no_purchase" and action == "purchase":
        return False, "🚫 Покупки временно недоступны."
    if mode == "no_register" and action == "register":
        return False, " Регистрация новых пользователей временно закрыта."
    return True, ""


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    tg_id = message.from_user.id

    try:
        await message.edit_reply_markup(
            reply_markup=ReplyKeyboardRemove(remove_keyboard=True)
        )
    except Exception:
        pass

    try:
        await message.delete()
    except Exception:
        pass

    maintenance = await dal.get_setting(session, "maintenance", "0")
    if maintenance == "1" and tg_id not in settings.admin_ids:
        await message.answer("🔧 Ведутся технические работы. Попробуйте позже.", disable_notification=True)
        return

    referred_by = None
    args = message.text.split(maxsplit=1) if message.text else []
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            ref_id = int(args[1][4:])
            if ref_id != tg_id:
                referred_by = ref_id
        except ValueError:
            pass

    user = await dal.get_user(session, tg_id)
    if not user:
        user = await dal.create_user(
            session, tg_id,
            username=message.from_user.username,
            referred_by=referred_by,
        )
    elif referred_by and not user.referred_by:
        await dal.update_user(session, tg_id, referred_by=referred_by)

    if message.from_user.username and user.username != message.from_user.username:
        await dal.update_user(session, tg_id, username=message.from_user.username)

    kb, status_line = await _get_menu_context(session, tg_id, user.remnawave_uuid)
    text = _welcome_text(status_line)
    photo_url = settings.WELCOME_IMAGE_URL if settings.WELCOME_IMAGE_URL else None

    await show_menu_message(message, text, reply_markup=kb, photo_url=photo_url)

    if user.is_registered:
        return

    rw_user = await remnawave.get_user_by_telegram_id(tg_id)
    if rw_user and rw_user.username != f"user{tg_id}":
        await dal.update_user(
            session, tg_id,
            remnawave_username=rw_user.username,
            remnawave_uuid=str(rw_user.uuid),
            is_registered=True,
        )
        await remnawave.add_user_to_default_squad(str(rw_user.uuid))
        return

    allowed, error = await _check_access(session, tg_id, "register")
    if not allowed:
        await message.answer(error, disable_notification=True)
        return

    await _start_registration(message, session, state)


# ── Главное меню (callback) ───────────────────────────────────────────────────

@router.callback_query(F.data == "main_menu")
async def main_menu_cb(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    await state.clear()
    user = await dal.get_user(session, callback.from_user.id)
    uuid = user.remnawave_uuid if user else None
    kb, status_line = await _get_menu_context(session, callback.from_user.id, uuid)
    text = _welcome_text(status_line)
    photo_url = settings.WELCOME_IMAGE_URL if settings.WELCOME_IMAGE_URL else None
    await show_menu_message(callback, text, reply_markup=kb, photo_url=photo_url)


# ── Регистрация ──────────────────────────────────────────────────────────────

async def _start_registration(message: Message, session: AsyncSession, state: FSMContext):
    tg_username = message.from_user.username
    if tg_username:
        exists = await remnawave.username_exists(tg_username)
        if not exists:
            await _finish_registration(message, session, tg_username, message.from_user.id)
            return
        msg = await message.answer(
            f"️ Имя <code>@{tg_username}</code> уже занято.\n\n"
            f"Введите другое имя (только латиница, цифры, _): ",
            parse_mode="HTML",
            reply_markup=cancel_kb("main_menu"),
            disable_notification=True,
        )
    else:
        msg = await message.answer(
            "👤 У вас не установлен username в Telegram.\n\n"
            "Придумайте имя для аккаунта (только латиница, цифры, _): ",
            reply_markup=cancel_kb("main_menu"),
            disable_notification=True,
        )
    await state.update_data(bot_prompt_msg_id=msg.message_id)
    await state.set_state(RegistrationSG.choose_username)


@router.message(RegistrationSG.choose_username, ~F.text)
async def registration_wrong_type(message: Message, state: FSMContext):
    """На этапе регистрации принимаем только текст."""
    try:
        await message.delete()
    except Exception:
        pass
    data = await state.get_data()
    if prompt_id := data.get("bot_prompt_msg_id"):
        try:
            await message.bot.delete_message(message.chat.id, prompt_id)
        except Exception:
            pass
    msg = await message.answer(
        "⌨️ На этом шаге нужно ввести текстовое имя аккаунта.\n\nТолько латиница, цифры и _ (от 3 до 32 символов).",
        disable_notification=True,
        reply_markup=cancel_kb("main_menu"),
    )
    await state.update_data(bot_prompt_msg_id=msg.message_id)


@router.message(RegistrationSG.choose_username, F.text)
async def process_username_input(message: Message, session: AsyncSession, state: FSMContext):
    username = message.text.strip().lstrip("@").lower()
    if not re.match(r'^[a-z0-9_]{3,32}$', username):
        await cleanup_fsm_interaction(message, state)
        msg = await message.answer("❌ От 3 до 32 символов: только латиница, цифры и _.", disable_notification=True)
        await state.update_data(bot_prompt_msg_id=msg.message_id)
        return
    if await remnawave.username_exists(username):
        await cleanup_fsm_interaction(message, state)
        msg = await message.answer(f"❌ Имя <code>{username}</code> уже занято.", parse_mode="HTML", disable_notification=True)
        await state.update_data(bot_prompt_msg_id=msg.message_id)
        return
    if await dal.get_user_by_remnawave_username(session, username):
        await cleanup_fsm_interaction(message, state)
        msg = await message.answer("❌ Это имя уже используется.", disable_notification=True)
        await state.update_data(bot_prompt_msg_id=msg.message_id)
        return
    await cleanup_fsm_interaction(message, state)
    await _finish_registration(message, session, username, message.from_user.id)
    await state.clear()


async def _finish_registration(message: Message, session: AsyncSession, username: str, tg_id: int):
    await dal.update_user(session, tg_id, remnawave_username=username, is_registered=True)
    kb = main_menu_kb(is_admin=tg_id in settings.admin_ids)
    await message.answer(
        f"✅ Аккаунт зарегистрирован: <code>{username}</code>.\n\nТеперь можете оформить подписку.",
        parse_mode="HTML",
        reply_markup=kb,
        disable_notification=True,
    )


# ── Подписка ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "revoke_subscription")
async def revoke_subscription_prompt(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Перевыпустить", callback_data="revoke_subscription_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="main_menu")],
    ])
    await edit_or_answer(callback,
        "⚠️ <b>Перевыпустить ссылку?</b>\n\n"
        "Старая ссылка перестанет работать на подключённых устройствах.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "revoke_subscription_confirm")
async def revoke_subscription_confirm(callback: CallbackQuery, session: AsyncSession):
    user = await dal.get_user(session, callback.from_user.id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    rw = await remnawave.revoke_subscription(user.remnawave_uuid)
    if not rw:
        await callback.answer("Ошибка при перевыпуске ссылки", show_alert=True)
        return

    remnawave.invalidate_sub_info_cache(user.remnawave_uuid)

    await edit_or_answer(callback,
        "✅ <b>Ссылка перевыпущена</b>\n\nСтарая ссылка больше не действует.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Открыть подписку", url=rw.subscription_url)],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
        ]),
    )


# ── Устройства ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "my_devices")
async def my_devices(callback: CallbackQuery, session: AsyncSession):
    user = await dal.get_user(session, callback.from_user.id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    devices = await remnawave.get_user_devices(user.remnawave_uuid)
    rw = await remnawave.get_subscription_info(user.remnawave_uuid)
    limit = rw.hwid_device_limit if rw else 0
    limit_str = "∞" if not limit else str(limit)
    show_buy = settings.DEVICE_SLOT_PRICE > 0
    text = f"📱 <b>Устройства</b>\nИспользуется: {len(devices)} / {limit_str}"
    if not devices:
        text += "\n\nУстройств не зарегистрировано."
    await edit_or_answer(callback, text, reply_markup=devices_kb(devices, show_buy_slot=show_buy))


@router.callback_query(F.data.startswith("device:"))
async def device_detail(callback: CallbackQuery, session: AsyncSession):
    user = await dal.get_user(session, callback.from_user.id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    hwid = callback.data.split(":", 1)[1]
    devices = await remnawave.get_user_devices(user.remnawave_uuid)
    device = next((d for d in devices if d.hwid == hwid), None)
    if not device:
        await callback.answer("Устройство не найдено — уже удалено", show_alert=True)
        await my_devices(callback, session)
        return
    text = (
        f"{_device_icon(device.platform)} <b>{device.platform or 'Неизвестно'}</b>\n\n"
        f"Статус: 🟢 Активно\n"
        f"Модель: {device.device_model or '—'}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить устройство", callback_data=f"delete_device_prompt:{hwid}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="my_devices")],
    ])
    await edit_or_answer(callback, text, reply_markup=kb)


@router.callback_query(F.data.startswith("delete_device_prompt:"))
async def delete_device_prompt(callback: CallbackQuery):
    hwid = callback.data.split(":", 1)[1]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_device:{hwid}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"device:{hwid}")],
    ])
    await edit_or_answer(callback,
        "⚠️ <b>Удалить устройство?</b>\n\nПосле удаления потребуется подключить его заново.",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("delete_device:"))
async def delete_device(callback: CallbackQuery, session: AsyncSession):
    user = await dal.get_user(session, callback.from_user.id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    hwid = callback.data.split(":", 1)[1]
    ok = await remnawave.delete_user_device(user.remnawave_uuid, hwid)
    if ok:
        await callback.answer("✅ Устройство удалено")
        await my_devices(callback, session)
    else:
        await callback.answer("❌ Ошибка при удалении", show_alert=True)


@router.callback_query(F.data == "delete_all_devices")
async def delete_all_devices_prompt(callback: CallbackQuery, session: AsyncSession):
    user = await dal.get_user(session, callback.from_user.id)
    devices = await remnawave.get_user_devices(user.remnawave_uuid) if user and user.remnawave_uuid else []
    names = "\n".join(f"• {d.platform or 'Неизвестно'}" for d in devices) or "—"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить все", callback_data="delete_all_devices_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="my_devices")],
    ])
    await edit_or_answer(callback,
        f"⚠️ <b>Удалить все устройства?</b>\n\nБудут удалены:\n{names}\n\n"
        f"После удаления потребуется подключить их заново.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "delete_all_devices_confirm")
async def delete_all_devices(callback: CallbackQuery, session: AsyncSession):
    user = await dal.get_user(session, callback.from_user.id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    ok = await remnawave.delete_all_user_devices(user.remnawave_uuid)
    if ok:
        await callback.answer("✅ Все устройства удалены")
        await my_devices(callback, session)
    else:
        await callback.answer("❌ Ошибка при удалении", show_alert=True)


# ── История платежей ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "payment_history")
async def payment_history(callback: CallbackQuery, session: AsyncSession):
    user = await dal.get_user(session, callback.from_user.id)
    if not user:
        await callback.answer()
        return
    from sqlalchemy import select
    from db.models import Payment
    from sqlalchemy.orm import selectinload
    result = await session.execute(
        select(Payment).options(selectinload(Payment.tariff))
        .where(Payment.user_id == user.id)
        .order_by(Payment.created_at.desc())
    )
    payments = result.scalars().all()
    s_emoji = {"pending": "", "approved": "✅", "rejected": "❌"}
    if not payments:
        text = "💳 <b>История платежей</b>\n\nПлатежей пока нет."
    else:
        lines = ["💳 <b>История платежей:</b>\n"]
        for p in payments[:10]:
            tariff_name = (
                p.tariff.name if p.tariff
                else ("доп. устройство" if p.payment_type == "device_slot" else "?")
            )
            lines.append(
                f"{s_emoji.get(p.status,'❓')} {p.created_at.strftime('%d.%m.%Y')} — "
                f"{int(p.amount)} ₽ ({tariff_name})"
            )
        text = "\n".join(lines)
    await edit_or_answer(callback, text, reply_markup=back_kb("main_menu"))


# ── Реферальная программа ─────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_invite")
async def menu_invite(callback: CallbackQuery, session: AsyncSession):
    tg_id = callback.from_user.id
    bot_info = await callback.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{tg_id}"
    ref_rate = int(await dal.get_setting(session, "referral_days", "0"))
    ref_count = await dal.count_referrals(session, tg_id)
    ref_paid = await dal.get_referrals_with_payment(session, tg_id)
    bonus_text = (
        f"\n🎁 Друг получает скидку {settings.REFERRAL_DISCOUNT_PERCENT:g}% на первую покупку.\n"
        f"💰 Вам начисляется <b>{ref_rate} дн.</b> за каждые 30 дней тарифа, который он купит."
        if ref_rate else ""
    )

    await edit_or_answer(callback,
        f"👥 <b>Реферальная программа</b>\n\n"
        f"Приглашайте друзей и получайте бонусы за их подписки.\n\n"
        f"Ваша ссылка:\n<code>{link}</code>"
        f"{bonus_text}\n\n"
        f"📊 Приглашено: {ref_count} | Оплатили: {len(ref_paid)}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Скопировать ссылку", copy_text=CopyTextButton(text=link))],
            [InlineKeyboardButton(text="📤 Поделиться", switch_inline_query="invite")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")],
        ]),
    )


# ── Proxy для Telegram ────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_proxy")
async def menu_proxy(callback: CallbackQuery, session: AsyncSession):
    user = await dal.get_user(session, callback.from_user.id)
    if not user or not user.remnawave_uuid or not user.mtproto_secret:
        await callback.answer("Прокси недоступен. Оформите подписку.", show_alert=True)
        return
    rw = await remnawave.get_subscription_info(user.remnawave_uuid)
    if not _has_active_proxy_access(rw):
        await callback.answer(
            "Прокси недоступен. Оформите подписку.",
            show_alert=True,
        )
        return
    from bot.services import telemt as telemt_svc
    # Ссылка детерминирована: секрет + хост + порт. API не нужен.
    link = telemt_svc.build_link_fallback(user.mtproto_secret)
    if not link:
        await callback.answer("Не удалось получить ссылку.", show_alert=True)
        return

    await edit_or_answer(callback,
        "📡 <b>Proxy для Telegram</b>\n\n"
        "Нажмите кнопку чтобы подключить прокси в Telegram.\n\n"
        "⚠️ <b>Ссылка персональная.</b> Не передавайте её другим — при обнаружении посторонних подключений ссылка будет сброшена.\n\n"
        "🔒 Деактивируется автоматически если подписка не оплачена более 5 дней.",
        parse_mode="HTML",
        reply_markup=proxy_kb(link),
    )
    
@router.callback_query(F.data == "revoke_mtproxy")
async def revoke_mtproxy_prompt(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Перевыпустить", callback_data="revoke_mtproxy_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="menu_proxy")],
    ])
    await edit_or_answer(callback,
        "⚠️ <b>Перевыпустить ссылку прокси?</b>\n\n"
        "Старая ссылка перестанет работать на подключённых устройствах.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "revoke_mtproxy_confirm")
async def revoke_mtproxy(callback: CallbackQuery, session: AsyncSession):
    from bot.services import telemt as telemt_svc
    
    user = await dal.get_user(session, callback.from_user.id)
    if not user or not user.mtproto_secret or not user.remnawave_uuid:
        await callback.answer("Прокси недоступен.", show_alert=True)
        return
    
    rw = await remnawave.get_subscription_info(user.remnawave_uuid)
    if not rw:
        await callback.answer("Не удалось получить информацию о подписке.", show_alert=True)
        return
    
    # Если лимит не установлен (0) — ставим 5 для поддержки сторонних клиентов
    max_ips = max(1, rw.hwid_device_limit) if rw.hwid_device_limit else 5
    
    # Генерируем новый секрет
    new_secret = telemt_svc.generate_secret()
    
    # Сначала удаляем старого пользователя, чтобы telemt принял новый секрет
    await telemt_svc.remove_user(user.remnawave_username)
    # Обновляем в telemt API
    try:
        await telemt_svc.add_user(user.remnawave_username, new_secret, max_ips=max_ips)
        await dal.update_user(session, user.telegram_id, mtproto_secret=new_secret)
        
        # Строим новую ссылку
        link = telemt_svc.build_link_fallback(new_secret)
        
        await edit_or_answer(callback,
            "📡 <b>Ссылка перевыпущена</b>\n\n"
            "Старая ссылка больше не работает. Используйте новую:",
            parse_mode="HTML",
            reply_markup=proxy_kb(link),
        )
    except Exception as e:
        logger.warning(f"MTProxy revoke failed for {user.telegram_id}: {e}")
        await callback.answer("Ошибка при перевыпуске.", show_alert=True)


# ── Поддержка (вход через меню) ───────────────────────────────────────────────

TICKET_STATUS_EMOJI = {"open": "🟡", "closed": "🟢"}


def _ticket_preview(ticket) -> str:
    first_text = next((m.text for m in ticket.messages if m.text), None) or "[без текста]"
    return first_text[:40] + ("…" if len(first_text) > 40 else "")


@router.callback_query(F.data == "menu_support")
async def menu_support(callback: CallbackQuery, session: AsyncSession):
    user = await dal.get_user(session, callback.from_user.id)
    if not user or not user.is_registered:
        await callback.answer("Сначала зарегистрируйтесь — нажмите /start", show_alert=True)
        return

    tickets = await dal.get_user_tickets(session, user.id, limit=10)
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Создать обращение", callback_data="create_ticket")
    for t in tickets:
        emoji = TICKET_STATUS_EMOJI.get(t.status, "⚪")
        builder.button(text=f"{emoji} #{t.id} · {_ticket_preview(t)}", callback_data=f"view_my_ticket:{t.id}")
    builder.button(text="◀️ Назад", callback_data="main_menu")
    builder.adjust(1)

    text = "💬 <b>Поддержка</b>\n\nЕсть вопрос или проблема?"
    if tickets:
        text += "\n\n<b>Ваши обращения:</b>"

    await edit_or_answer(callback, text, parse_mode="HTML", reply_markup=builder.as_markup())


@router.callback_query(F.data == "create_ticket")
async def create_ticket_start(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    user = await dal.get_user(session, callback.from_user.id)
    if not user:
        await callback.answer()
        return

    ticket = await dal.get_open_ticket(session, user.id)
    is_new = False
    if not ticket:
        ticket = await dal.create_ticket(session, user.id)
        is_new = True

    await state.set_state(SupportSG.waiting_message)
    await state.update_data(ticket_id=ticket.id)

    text = (
        f"💬 <b>Поддержка</b>\n\n"
        f"Тикет #{ticket.id} открыт.\n"
        f"Опишите вашу проблему — можно приложить фото или видео.\n\n"
        f"Чтобы закончить диалог, нажмите «Закрыть обращение» или отправьте /close"
        if is_new else
        f"💬 <b>Поддержка</b>\n\n"
        f"У вас уже открыт тикет #{ticket.id}.\n"
        f"Напишите сообщение — оно добавится в этот тикет."
    )

    msg = await edit_or_answer(callback,
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔒 Закрыть обращение", callback_data="close_my_ticket")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="menu_support")],
        ]),
    )
    await state.update_data(bot_prompt_msg_id=msg.message_id if msg else None)


@router.callback_query(F.data.startswith("view_my_ticket:"))
async def view_my_ticket(callback: CallbackQuery, session: AsyncSession):
    user = await dal.get_user(session, callback.from_user.id)
    ticket_id = int(callback.data.split(":")[1])
    ticket = await dal.get_ticket_by_id(session, ticket_id)
    if not ticket or not user or ticket.user_id != user.id:
        await callback.answer("Обращение не найдено", show_alert=True)
        return

    emoji = TICKET_STATUS_EMOJI.get(ticket.status, "⚪")
    status_label = "Открыт" if ticket.status == "open" else "Закрыт"
    lines = [f"🎫 <b>Обращение #{ticket.id}</b>\nСтатус: {emoji} {status_label}\n"]
    for m in ticket.messages[-10:]:
        who = "👤 Вы" if m.sender_role == "user" else "🛡 Поддержка"
        lines.append(f"{who}: {m.text or f'[{m.media_type}]'}")

    builder = InlineKeyboardBuilder()
    if ticket.status == "open":
        builder.button(text="✏️ Ответить", callback_data="create_ticket")
        builder.button(text="🔒 Закрыть обращение", callback_data="close_my_ticket")
    builder.button(text="◀️ Назад", callback_data="menu_support")
    builder.adjust(1)

    await edit_or_answer(callback, "\n".join(lines), parse_mode="HTML", reply_markup=builder.as_markup())


@router.callback_query(F.data == "close_my_ticket")
async def close_my_ticket(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    if not ticket_id:
        user = await dal.get_user(session, callback.from_user.id)
        if user:
            ticket = await dal.get_open_ticket(session, user.id)
            if ticket:
                ticket_id = ticket.id

    if ticket_id:
        await dal.close_ticket(session, ticket_id)
        await notify_admins(callback.bot, f"🔒 Тикет #{ticket_id} закрыт пользователем.")
    await state.clear()
    await callback.answer("✅ Тикет закрыт")

    await menu_support(callback, session)


# ── Inline-режим ──────────────────────────────────────────────────────────────

@router.inline_query(F.query.lower() == "invite")
async def inline_invite(inline_query: InlineQuery):
    tg_id = inline_query.from_user.id
    bot_info = await inline_query.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{tg_id}"
    text = (
        "— Алло, интернет опять не работает.\n"
        "— А VPN включён?\n"
        "— Да.\n"
        "— Тогда выключи и включи.\n\n"
        f"Надоел этот ритуал? 🙃\n\n"
        f"{settings.BOT_NAME} — VPN, который работает без шаманских обрядов.\n\n"
        "💻 Несколько устройств\n🌐 Безлимитный трафик\n⚡️ Быстрая скорость"
    )
    result = InlineQueryResultArticle(
        id="invite",
        title="Поделиться ссылкой",
        description="Отправить реферальный баннер в чат",
        input_message_content=InputTextMessageContent(message_text=text, parse_mode="HTML"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Присоединиться", url=link)]
        ]),
    )
    await inline_query.answer([result], cache_time=30, is_personal=True)


# ── Перехват неизвестных сообщений (глобальные) ──────────────────────────────

@router.message(F.voice | F.video_note, StateFilter(None))
async def catch_voice_global(message: Message, state: FSMContext):
    """Голосовые и кружочки запрещены везде вне FSM."""
    tg_id = message.from_user.id
    try:
        await message.delete()
    except Exception:
        pass

    if tg_id in _notification_cache:
        return
    _notification_cache[tg_id] = True

    msg = await message.answer(
        "🎙 <b>Голосовые и кружки не принимаются.</b>\n\n"
        "Для обращения в поддержку используйте кнопку ниже.",
        parse_mode="HTML",
        disable_notification=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Написать в поддержку", callback_data="menu_support")]
        ]),
    )
    asyncio.create_task(delete_later(message.bot, message.chat.id, msg.message_id, 30))


@router.message(F.sticker, StateFilter(None))
async def catch_sticker_global(message: Message, state: FSMContext):
    """Стикеры запрещены везде вне FSM."""
    tg_id = message.from_user.id
    try:
        await message.delete()
    except Exception:
        pass

    if tg_id in _notification_cache:
        return
    _notification_cache[tg_id] = True

    msg = await message.answer(
        "🙅 <b>Стикеры не принимаются.</b>\n\n"
        "Для обращения в поддержку используйте кнопку ниже.",
        parse_mode="HTML",
        disable_notification=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Написать в поддержку", callback_data="menu_support")]
        ]),
    )
    asyncio.create_task(delete_later(message.bot, message.chat.id, msg.message_id, 30))


@router.message(F.photo | F.video | F.animation | F.document | F.contact | F.location, StateFilter(None))
async def catch_media_global(message: Message, state: FSMContext):
    """Медиа вне FSM — удаляем и показываем плашку."""
    tg_id = message.from_user.id
    try:
        await message.delete()
    except Exception:
        pass

    if tg_id in _notification_cache:
        return
    _notification_cache[tg_id] = True

    msg = await message.answer(
        "📎 <b>Сообщение не принято.</b>\n\n"
        "Для обращения в поддержку используйте кнопку ниже.",
        parse_mode="HTML",
        disable_notification=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Написать в поддержку", callback_data="menu_support")]
        ]),
    )
    asyncio.create_task(delete_later(message.bot, message.chat.id, msg.message_id, 30))


@router.message(F.text & ~F.text.startswith("/"), StateFilter(None))
async def catch_text_global(message: Message, session: AsyncSession, state: FSMContext):
    """Произвольный текст вне FSM — удаляем и показываем плашку."""
    tg_id = message.from_user.id
    try:
        await message.delete()
    except Exception:
        pass

    if tg_id in _notification_cache:
        return
    _notification_cache[tg_id] = True

    msg = await message.answer(
        "💬 <b>Бот работает через кнопки меню.</b>\n\n"
        "Если нужна помощь — обратитесь в поддержку.",
        parse_mode="HTML",
        disable_notification=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Написать в поддержку", callback_data="menu_support")]
        ]),
    )
    asyncio.create_task(delete_later(message.bot, message.chat.id, msg.message_id, 30))


# ── Отмена ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    user = await dal.get_user(session, callback.from_user.id)
    uuid = user.remnawave_uuid if user else None
    kb, status_line = await _get_menu_context(session, callback.from_user.id, uuid)
    photo_url = settings.WELCOME_IMAGE_URL if settings.WELCOME_IMAGE_URL else None
    await show_menu_message(callback, _welcome_text(status_line), reply_markup=kb, photo_url=photo_url)
