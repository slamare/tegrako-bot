from aiogram import Router, F
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    ReplyKeyboardRemove,
)
from sqlalchemy.ext.asyncio import AsyncSession
from cachetools import TTLCache
import re
import asyncio
from datetime import datetime, timezone

from bot.states.states import RegistrationSG, SupportSG
from bot.keyboards.user_kb import (
    main_menu_kb, back_kb, nav_kb, profile_kb, proxy_kb,
    devices_kb, subscription_detail_kb, cancel_kb, remove_kb,
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


async def _get_menu_kb(session, tg_id: int, remnawave_uuid: str | None) -> InlineKeyboardMarkup:
    is_adm = tg_id in settings.admin_ids
    show_proxy = False
    if remnawave_uuid:
        try:
            rw = await remnawave.get_subscription_info(remnawave_uuid)
            show_proxy = _has_active_proxy_access(rw)
        except Exception:
            pass
    return main_menu_kb(is_admin=is_adm, show_proxy=show_proxy)


def _welcome_text() -> str:
    return (
        f"👋 Главное меню\n\n"
        f"<b>{settings.BOT_NAME}</b> - Сервис для защиты соединения и обеспечения приватности в сети.\n"
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

    kb = await _get_menu_kb(session, tg_id, user.remnawave_uuid)
    text = _welcome_text()
    photo_url = settings.WELCOME_IMAGE_URL if settings.WELCOME_IMAGE_URL else None

    await show_menu_message(message, text, reply_markup=kb, photo_url=photo_url)

    if user.is_registered:
        return

    rw_user = await remnawave.get_user_by_telegram_id(tg_id)
    if rw_user:
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
    kb = await _get_menu_kb(session, callback.from_user.id, uuid)
    text = _welcome_text()
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


# ── Профиль ───────────────────────────────────────────────────────────────────

async def _profile_text_and_kb(session, tg_id: int):
    user = await dal.get_user(session, tg_id)
    if not user or not user.is_registered:
        return None, None
    has_sub = bool(user.remnawave_uuid)
    sub_info = ""
    if has_sub:
        try:
            rw = await remnawave.get_subscription_info(user.remnawave_uuid)
            if rw:
                now = datetime.now(timezone.utc)
                days_left = (rw.expire_at - now).days
                expire_str = rw.expire_at.strftime("%d.%m.%Y")
                used_gb = round(rw.user_traffic.used_traffic_bytes / 1024 ** 3, 2)
                limit_gb = round(rw.traffic_limit_bytes / 1024 ** 3, 1) if rw.traffic_limit_bytes else "∞"
                s_emoji = {"ACTIVE": "🟢", "EXPIRED": "🔴", "DISABLED": ""}.get(rw.status.value, "⚪")
                sub_info = (
                    f"\n\n<b>Подписка:</b>\n"
                    f"{s_emoji} Статус: {rw.status.value}\n"
                    f"📅 До: {expire_str} ({days_left} дн.)\n"
                    f" Трафик: {used_gb} / {limit_gb} ГБ"
                )
        except Exception:
            sub_info = "\n\n⚠️ Не удалось получить данные подписки"

    ref_count = await dal.count_referrals(session, tg_id)
    ref_paid = await dal.get_referrals_with_payment(session, tg_id)

    text = (
        f"👤 <b>Управление подпиской</b>\n\n"
        f"🆔 ID: <code>{tg_id}</code>\n"
        f"👤 Аккаунт: <code>{user.remnawave_username}</code>"
        f"{sub_info}"
        f"\n\n👥 Рефералов: {ref_count} (оплатили: {len(ref_paid)})"
    )
    return text, profile_kb(has_sub)


@router.callback_query(F.data == "menu_profile")
async def menu_profile(callback: CallbackQuery, session: AsyncSession):
    text, kb = await _profile_text_and_kb(session, callback.from_user.id)
    if not text:
        await callback.answer("Сначала зарегистрируйтесь — нажмите /start", show_alert=True)
        return
    await edit_or_answer(callback, text, reply_markup=kb)


# ── Подписка ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "my_subscription")
async def my_subscription(callback: CallbackQuery, session: AsyncSession):
    user = await dal.get_user(session, callback.from_user.id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    rw = await remnawave.get_subscription_info(user.remnawave_uuid)
    if not rw:
        await callback.answer("Не удалось получить данные", show_alert=True)
        return
    await edit_or_answer(callback,
        f" <b>Ваша подписка</b>\n\n"
        f"Нажмите кнопку ниже чтобы открыть ссылку подключения.\n\n"
        f"⚠️ <b>Сброс ссылки</b> — сгенерирует новую. Старая перестанет работать.",
        parse_mode="HTML",
        reply_markup=subscription_detail_kb(rw.subscription_url),
    )


@router.callback_query(F.data == "revoke_subscription")
async def revoke_subscription_prompt(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, сбросить", callback_data="revoke_subscription_confirm")],
        [InlineKeyboardButton(text=" Отмена", callback_data="my_subscription")],
    ])
    await edit_or_answer(callback,
        "⚠️ <b>Подтвердите сброс ссылки</b>\n\n"
        "Старая ссылка перестанет работать. Нужно обновить её во всех приложениях.",
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
        await callback.answer("Ошибка при сбросе ссылки", show_alert=True)
        return

    remnawave.invalidate_sub_info_cache(user.remnawave_uuid)

    await edit_or_answer(callback,
        "✅ <b>Ссылка обновлена!</b>\n\nОбновите подписку во всех приложениях.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Открыть новую подписку", url=rw.subscription_url)],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="menu_profile")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")],
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
    text = f"📱 <b>Мои устройства</b> ({len(devices)}/{limit_str})\n\n"
    if devices:
        for i, d in enumerate(devices, 1):
            platform = d.platform or "Неизвестно"
            model = d.device_model or "—"
            text += f"{i}. {platform} — {model}\n"
    else:
        text += "Устройств не зарегистрировано."

    await edit_or_answer(callback, text, reply_markup=devices_kb(devices, show_buy_slot=show_buy))


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
async def delete_all_devices_prompt(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить все", callback_data="delete_all_devices_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="my_devices")],
    ])
    await edit_or_answer(callback,
        "️ <b>Удалить все устройства?</b>\n\n"
        "После этого нужно заново авторизоваться на всех устройствах.",
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
    await edit_or_answer(callback, text, reply_markup=nav_kb("menu_profile"))


# ── Реферальная программа ─────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_invite")
async def menu_invite(callback: CallbackQuery, session: AsyncSession):
    tg_id = callback.from_user.id
    bot_info = await callback.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{tg_id}"
    ref_days = int(await dal.get_setting(session, "referral_days", "0"))
    ref_count = await dal.count_referrals(session, tg_id)
    ref_paid = await dal.get_referrals_with_payment(session, tg_id)
    bonus_text = (
        f"\n💰 За каждого оплатившего друга — <b>+{ref_days} дней</b>. " if ref_days else ""
    )

    await edit_or_answer(callback,
        f"👥 <b>Реферальная программа</b>\n\n"
        f"Ваша ссылка:\n<code>{link}</code>"
        f"{bonus_text}\n\n"
        f"📊 Приглашено: {ref_count} | Оплатили: {len(ref_paid)}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 Поделиться", switch_inline_query="invite")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")],
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

@router.callback_query(F.data == "menu_support")
async def menu_support(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    user = await dal.get_user(session, callback.from_user.id)
    if not user or not user.is_registered:
        await callback.answer("Сначала зарегистрируйтесь — нажмите /start", show_alert=True)
        return
    await state.set_state(SupportSG.waiting_message)

    msg = await edit_or_answer(callback,
        f"💬 <b>Поддержка</b>\n\n"
        f"Напишите ваш вопрос — ответим как можно скорее.\n\n"
        f"Тикет будет создан автоматически после вашего первого сообщения.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=" Меню", callback_data="main_menu")],
        ]),
    )
    await state.update_data(bot_prompt_msg_id=msg.message_id if msg else None)


@router.message(SupportSG.waiting_message, F.text)
async def support_message(message: Message, session: AsyncSession, state: FSMContext):
    user = await dal.get_user(session, message.from_user.id)
    if not user:
        return
    ticket = await dal.get_open_ticket(session, user.id)
    is_new_ticket = False
    if not ticket:
        ticket = await dal.create_ticket(session, user.id)
        is_new_ticket = True

    await dal.add_ticket_message(
        session, ticket_id=ticket.id, sender_role="user",
        sender_tg_id=message.from_user.id, text=message.text,
    )

    await state.update_data(ticket_id=ticket.id)

    if is_new_ticket:
        for admin_id in settings.admin_ids:
            try:
                await message.bot.send_message(
                    admin_id,
                    f"🎫 <b>Новый тикет #{ticket.id}</b>\n\n"
                    f"👤 @{user.username or '—'} (<code>{user.telegram_id}</code>)\n"
                    f"🆔 <code>{user.remnawave_username or '—'}</code>\n\n"
                    f"💬 <b>Сообщение:</b>\n{message.text}",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    await cleanup_fsm_interaction(message, state)

    msg = await message.answer(
        f"✅ Сообщение принято! Тикет #{ticket.id}.\n\n"
        f"Ожидайте ответа поддержки.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔒 Закрыть тикет", callback_data="close_my_ticket")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")],
        ]),
        disable_notification=True,
    )
    await state.clear()
    asyncio.create_task(delete_later(message.bot, message.chat.id, msg.message_id, 30))


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
        for admin_id in settings.admin_ids:
            try:
                await callback.bot.send_message(admin_id, f"🔒 Тикет #{ticket_id} закрыт пользователем.")
            except Exception:
                pass
    await state.clear()
    await callback.answer("✅ Тикет закрыт")

    user = await dal.get_user(session, callback.from_user.id)
    uuid = user.remnawave_uuid if user else None
    kb = await _get_menu_kb(session, callback.from_user.id, uuid)
    photo_url = settings.WELCOME_IMAGE_URL if settings.WELCOME_IMAGE_URL else None
    await show_menu_message(callback, _welcome_text(), reply_markup=kb, photo_url=photo_url)


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
        "💻 Несколько устройств\n🌐 Безлимитный трафик\n Быстрая скорость"
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
    kb = await _get_menu_kb(session, callback.from_user.id, uuid)
    photo_url = settings.WELCOME_IMAGE_URL if settings.WELCOME_IMAGE_URL else None
    await show_menu_message(callback, _welcome_text(), reply_markup=kb, photo_url=photo_url)