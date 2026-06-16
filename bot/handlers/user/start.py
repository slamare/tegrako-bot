from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
)
from sqlalchemy.ext.asyncio import AsyncSession
import re
from datetime import datetime, timezone
from bot.states.states import RegistrationSG
from bot.keyboards.user_kb import main_menu_kb, back_kb, profile_kb, proxy_kb, devices_kb
from bot.services import remnawave
from config.settings import settings
from db import dal

router = Router()


def _has_active_proxy_access(rw) -> bool:
    if not rw or rw.status.value != "ACTIVE":
        return False
    now = datetime.now(timezone.utc)
    return (rw.expire_at - now).days > 5


async def _build_main_menu(session, tg_id: int, remnawave_uuid: str | None) -> ReplyKeyboardMarkup:
    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
    show_proxy = False
    if remnawave_uuid:
        try:
            rw = await remnawave.get_subscription_info(remnawave_uuid)
            show_proxy = _has_active_proxy_access(rw)
        except Exception:
            pass
    is_admin = tg_id in settings.admin_ids
    return main_menu_kb(is_admin=is_admin, show_proxy=show_proxy)


DOCS_KB = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(
        text="📄 Политика конфиденциальности",
        url="https://telegra.ph/Politika-konfidencialnosti-04-01-26",
    ),
    InlineKeyboardButton(
        text="📋 Пользовательское соглашение",
        url="https://telegra.ph/Polzovatelskoe-soglashenie-04-01-19",
    ),
]])

# Кнопки меню и команды которые не должны перехватываться
KNOWN_BUTTONS = {
    "👤 Личный кабинет",
    "🛒 Купить подписку",
    "💬 Поддержка",
    "👥 Пригласить друга",
    "📡 Proxy для Telegram",
    "⚙️ Администратор",
}
KNOWN_COMMANDS = {"/start", "/admin", "/close", "/help", "/docs"}


async def _check_access(session, tg_id: int, action: str) -> tuple[bool, str]:
    if tg_id in settings.admin_ids:
        return True, ""
    mode = await dal.get_setting(session, "access_mode", "open")
    if mode == "open":
        return True, ""
    if mode == "closed":
        return False, "🔧 Сервис временно недоступен. Попробуйте позже."
    if mode == "invite_only" and action == "register":
        return False, "🔒 Регистрация доступна только по реферальной ссылке."
    if mode == "no_purchase" and action == "purchase":
        return False, "🚫 Покупки временно недоступны."
    if mode == "no_register" and action == "register":
        return False, "🔒 Регистрация новых пользователей временно закрыта."
    return True, ""


async def _custom_buttons_kb(session, has_sub: bool) -> InlineKeyboardMarkup | None:
    buttons = await dal.get_active_custom_buttons(session)
    filtered = [
        b for b in buttons
        if b.condition == "all" or (b.condition == "active_sub" and has_sub)
    ]
    if not filtered:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b.text, url=b.url)]
        for b in filtered
    ])


@router.message(Command("docs"))
@router.message(Command("help"))
async def cmd_docs(message: Message):
    await message.answer(
        "📄 <b>Документы</b>\n\nПолитика конфиденциальности и Пользовательское соглашение:",
        parse_mode="HTML",
        reply_markup=DOCS_KB,
    )


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    tg_id = message.from_user.id

    maintenance = await dal.get_setting(session, "maintenance", "0")
    if maintenance == "1" and tg_id not in settings.admin_ids:
        await message.answer("🔧 Ведутся технические работы. Попробуйте позже.")
        return

    referred_by = None
    args = message.text.split(maxsplit=1)
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

    welcome_text = (
        f"👋 Добро пожаловать в <b>{settings.BOT_NAME}</b>!\n\n"
        f"Сервис для защиты соединения и обеспечения приватности в сети.\n"
        f"Выберите действие в меню ниже."
    )

    menu_kb = await _build_main_menu(session, tg_id, user.remnawave_uuid if user else None)
    if settings.WELCOME_IMAGE_URL:
        try:
            img = settings.WELCOME_IMAGE_URL
            photo = (
                img if img.startswith("http")
                else __import__("aiogram.types", fromlist=["FSInputFile"]).FSInputFile(img)
            )
            await message.answer_photo(photo, caption=welcome_text, parse_mode="HTML", reply_markup=menu_kb)
        except Exception:
            await message.answer(welcome_text, parse_mode="HTML", reply_markup=menu_kb)
    else:
        await message.answer(welcome_text, parse_mode="HTML", reply_markup=menu_kb)

    has_sub = bool(user.remnawave_uuid) if user else False
    custom_kb = await _custom_buttons_kb(session, has_sub)
    if custom_kb:
        await message.answer("🔗 Полезные ссылки:", reply_markup=custom_kb)

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
        await message.answer(
            f"✅ Аккаунт найден: <code>{rw_user.username}</code>. Добро пожаловать!",
            parse_mode="HTML",
        )
        return

    allowed, error = await _check_access(session, tg_id, "register")
    if not allowed:
        await message.answer(error)
        return

    await _start_registration(message, session, state)


async def _start_registration(message: Message, session: AsyncSession, state: FSMContext):
    tg_username = message.from_user.username
    if tg_username:
        exists = await remnawave.username_exists(tg_username)
        if not exists:
            await _finish_registration(message, session, tg_username, message.from_user.id)
            return
        await message.answer(
            f"⚠️ Имя <code>@{tg_username}</code> уже занято.\n\n"
            f"Введите другое имя (только латиница, цифры, _):",
            parse_mode="HTML", reply_markup=back_kb("cancel"),
        )
    else:
        await message.answer(
            "👤 У вас не установлен username в Telegram.\n\n"
            "Придумайте имя для аккаунта (только латиница, цифры, _):",
            reply_markup=back_kb("cancel"),
        )
    await state.set_state(RegistrationSG.choose_username)


@router.message(RegistrationSG.choose_username)
async def process_username_input(message: Message, session: AsyncSession, state: FSMContext):
    username = message.text.strip().lstrip("@").lower()
    if not re.match(r'^[a-z0-9_]{3,32}$', username):
        await message.answer("❌ От 3 до 32 символов: только латиница, цифры и _. Попробуйте снова:")
        return
    if await remnawave.username_exists(username):
        await message.answer(f"❌ Имя <code>{username}</code> уже занято. Попробуйте другое:", parse_mode="HTML")
        return
    if await dal.get_user_by_remnawave_username(session, username):
        await message.answer("❌ Это имя уже используется. Попробуйте другое:")
        return
    await _finish_registration(message, session, username, message.from_user.id)
    await state.clear()


async def _finish_registration(message: Message, session: AsyncSession, username: str, tg_id: int):
    await dal.update_user(session, tg_id, remnawave_username=username, is_registered=True)
    await message.answer(
        f"✅ Аккаунт зарегистрирован: <code>{username}</code>.\n\nТеперь можете оформить подписку.",
        parse_mode="HTML",
        reply_markup=main_menu_kb(is_admin=tg_id in settings.admin_ids),
    )


# ── Личный кабинет ────────────────────────────────────────────────────────────

@router.message(F.text == "👤 Личный кабинет")
async def profile(message: Message, session: AsyncSession):
    tg_id = message.from_user.id
    user = await dal.get_user(session, tg_id)
    if not user or not user.is_registered:
        await message.answer("Сначала зарегистрируйтесь — нажмите /start")
        return

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
                limit_gb = (
                    round(rw.traffic_limit_bytes / 1024 ** 3, 1)
                    if rw.traffic_limit_bytes else "∞"
                )
                s_emoji = {"ACTIVE": "🟢", "EXPIRED": "🔴", "DISABLED": "⚫"}.get(rw.status.value, "⚪")
                sub_info = (
                    f"\n\n<b>Подписка:</b>\n"
                    f"{s_emoji} Статус: {rw.status.value}\n"
                    f"📅 До: {expire_str} ({days_left} дн.)\n"
                    f"📊 Трафик: {used_gb} / {limit_gb} ГБ"
                )
        except Exception:
            sub_info = "\n\n⚠️ Не удалось получить данные подписки"

    ref_count = await dal.count_referrals(session, tg_id)
    ref_paid = await dal.get_referrals_with_payment(session, tg_id)
    ref_info = f"\n\n👥 Рефералов: {ref_count} (оплатили: {len(ref_paid)})"

    await message.answer(
        f"👤 <b>Личный кабинет</b>\n\n"
        f"🆔 ID: <code>{tg_id}</code>\n"
        f"👤 Аккаунт: <code>{user.remnawave_username}</code>"
        f"{sub_info}"
        f"{ref_info}",
        parse_mode="HTML",
        reply_markup=profile_kb(has_sub),
    )


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

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Открыть подписку", url=rw.subscription_url)],
        [InlineKeyboardButton(text="🔄 Сбросить ссылку", callback_data="revoke_subscription")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_profile")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="back_main")],
    ])
    await callback.message.edit_text(
        f"📋 <b>Ваша подписка</b>\n\n"
        f"Нажмите кнопку ниже чтобы открыть ссылку подключения в браузере.\n\n"
        f"⚠️ <b>Сброс ссылки</b> — сгенерирует новую ссылку. Старая перестанет работать.",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data == "revoke_subscription")
async def revoke_subscription_confirm_prompt(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, сбросить", callback_data="revoke_subscription_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="my_subscription")],
    ])
    await callback.message.edit_text(
        "⚠️ <b>Подтвердите сброс ссылки</b>\n\n"
        "Старая ссылка подписки перестанет работать. "
        "Нужно будет обновить её во всех приложениях.",
        parse_mode="HTML", reply_markup=kb,
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

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Открыть новую подписку", url=rw.subscription_url)],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_profile")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="back_main")],
    ])
    await callback.message.edit_text(
        "✅ <b>Ссылка обновлена!</b>\n\nОбновите подписку во всех приложениях.",
        parse_mode="HTML", reply_markup=kb,
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
    show_buy = settings.DEVICE_SLOT_PRICE > 0

    limit_str = "∞" if not limit else str(limit)

    if not devices:
        text = (
            f"📱 <b>Мои устройства</b>\n\n"
            f"Устройств не зарегистрировано.\n"
            f"Лимит: {limit_str} уст."
        )
        kb = devices_kb([], limit, show_buy_slot=show_buy)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        return

    text = f"📱 <b>Мои устройства</b> ({len(devices)}/{limit_str})\n\n"
    for i, d in enumerate(devices, 1):
        platform = d.platform or "Неизвестно"
        model = d.device_model or "—"
        text += f"{i}. {platform} — {model}\n"

    kb = devices_kb(devices, limit, show_buy_slot=show_buy)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


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
    await callback.message.edit_text(
        "⚠️ <b>Удалить все устройства?</b>\n\n"
        "После этого нужно будет заново авторизоваться на всех устройствах.",
        parse_mode="HTML", reply_markup=kb,
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
        await callback.message.edit_text(
            "✅ <b>Все устройства удалены.</b>\n\n"
            "При следующем подключении устройство добавится автоматически.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_profile")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="back_main")],
            ]),
        )
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
    if not payments:
        text = "💳 <b>История платежей</b>\n\nПлатежей пока нет."
    else:
        s_emoji = {"pending": "⏳", "approved": "✅", "rejected": "❌"}
        lines = ["💳 <b>История платежей:</b>\n"]
        for p in payments[:10]:
            date_str = p.created_at.strftime("%d.%m.%Y")
            tariff_name = (
                p.tariff.name if p.tariff
                else ("доп. устройство" if p.payment_type == "device_slot" else "?")
            )
            lines.append(f"{s_emoji.get(p.status, '❓')} {date_str} — {int(p.amount)} ₽ ({tariff_name})")
        text = "\n".join(lines)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_profile")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="back_main")],
    ])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


# ── Реферальная система ───────────────────────────────────────────────────────

@router.message(F.text == "📡 Proxy для Telegram")
async def proxy_for_telegram(message: Message, session: AsyncSession):
    user = await dal.get_user(session, message.from_user.id)
    if not user or not user.remnawave_uuid or not user.mtproto_secret:
        await message.answer("📡 Прокси недоступен. Оформите подписку.")
        return

    rw = await remnawave.get_subscription_info(user.remnawave_uuid)
    if not _has_active_proxy_access(rw):
        await message.answer("📡 Прокси доступен только при активной подписке с запасом более 5 дней.")
        return

    from bot.services import telemt as telemt_svc
    link = await telemt_svc.get_proxy_link(user.remnawave_username)
    if not link:
        link = telemt_svc.build_link_fallback(user.mtproto_secret)

    if not link:
        await message.answer("Не удалось получить ссылку. Попробуйте позже.")
        return

    await message.answer(
        "📡 <b>Proxy для Telegram</b>\n\n"
        "Нажмите кнопку чтобы подключить прокси в Telegram.\n\n"
        "⚠️ <b>Ссылка персональная.</b> Не передавайте её другим — "
        "при обнаружении посторонних подключений ссылка будет сброшена.\n\n"
        "🔒 Деактивируется автоматически если подписка не оплачена более 5 дней.",
        parse_mode="HTML",
        reply_markup=proxy_kb(link),
    )


@router.message(F.text == "👥 Пригласить друга")
async def invite_friend(message: Message, session: AsyncSession):
    tg_id = message.from_user.id
    bot_info = await message.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{tg_id}"

    ref_days = int(await dal.get_setting(session, "referral_days", "0"))
    ref_count = await dal.count_referrals(session, tg_id)
    ref_paid = await dal.get_referrals_with_payment(session, tg_id)

    bonus_text = (
        f"\n🎁 За каждого оплатившего друга вы получаете <b>+{ref_days} дней</b>."
        if ref_days else ""
    )

    await message.answer(
        f"👥 <b>Реферальная программа</b>\n\n"
        f"Поделитесь ссылкой с друзьями:\n"
        f'<a href="{link}">{link}</a>'
        f"{bonus_text}\n\n"
        f"📊 Приглашено: {ref_count} | Оплатили: {len(ref_paid)}",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


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
        "Надоел этот ритуал? 🙃\n\n"
        f"{settings.BOT_NAME} — VPN, который не требует шаманских обрядов.\n\n"
        "💻 Несколько устройств\n"
        "🌐 Безлимитный трафик\n"
        "⚡ Быстрые серверы"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Присоединиться", url=link)]
    ])

    result = InlineQueryResultArticle(
        id="invite",
        title="Поделиться ссылкой на бот",
        description="Отправить реферальный баннер в чат",
        input_message_content=InputTextMessageContent(
            message_text=text,
            parse_mode="HTML",
        ),
        reply_markup=kb,
    )

    await inline_query.answer([result], cache_time=30, is_personal=True)


# ── Навигация ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "back_main")
async def back_to_main(callback: CallbackQuery, session: AsyncSession):
    # Удаляем inline-сообщение, пользователь возвращается к reply-меню
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "back_profile")
async def back_to_profile(callback: CallbackQuery, session: AsyncSession):
    user = await dal.get_user(session, callback.from_user.id)
    if not user:
        await callback.message.delete()
        await callback.answer()
        return

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
                limit_gb = (
                    round(rw.traffic_limit_bytes / 1024 ** 3, 1)
                    if rw.traffic_limit_bytes else "∞"
                )
                s_emoji = {"ACTIVE": "🟢", "EXPIRED": "🔴", "DISABLED": "⚫"}.get(rw.status.value, "⚪")
                sub_info = (
                    f"\n\n<b>Подписка:</b>\n"
                    f"{s_emoji} Статус: {rw.status.value}\n"
                    f"📅 До: {expire_str} ({days_left} дн.)\n"
                    f"📊 Трафик: {used_gb} / {limit_gb} ГБ"
                )
        except Exception:
            sub_info = "\n\n⚠️ Не удалось получить данные подписки"

    ref_count = await dal.count_referrals(session, callback.from_user.id)
    ref_paid = await dal.get_referrals_with_payment(session, callback.from_user.id)
    ref_info = f"\n\n👥 Рефералов: {ref_count} (оплатили: {len(ref_paid)})"

    await callback.message.edit_text(
        f"👤 <b>Личный кабинет</b>\n\n"
        f"🆔 ID: <code>{callback.from_user.id}</code>\n"
        f"👤 Аккаунт: <code>{user.remnawave_username}</code>"
        f"{sub_info}"
        f"{ref_info}",
        parse_mode="HTML",
        reply_markup=profile_kb(has_sub),
    )
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("Отменено")


# ── Перехват случайных сообщений ──────────────────────────────────────────────

@router.message(F.text)
async def catch_unknown_text(message: Message, session: AsyncSession):
    """
    Перехватывает любой текст не обработанный другими хендлерами.
    Удаляет сообщение пользователя и показывает подсказку.
    """
    text = message.text or ""

    # Пропускаем кнопки меню и команды — они обработаны выше
    if text in KNOWN_BUTTONS or text.startswith("/"):
        return

    # Удаляем сообщение пользователя
    try:
        await message.delete()
    except Exception:
        pass

    await message.answer(
        "💬 Для общения с поддержкой нажмите кнопку <b>«💬 Поддержка»</b> в меню.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Открыть поддержку", callback_data="open_support_inline")]
        ]),
    )


@router.callback_query(F.data == "open_support_inline")
async def open_support_inline(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    """Открывает тикет поддержки прямо из inline-кнопки подсказки."""
    from bot.handlers.user.support import open_support as _open_support
    # Удаляем подсказку
    try:
        await callback.message.delete()
    except Exception:
        pass
    # Передаём управление хендлеру поддержки через fake message
    await callback.answer()
    # Открываем тикет вручную
    user = await dal.get_user(session, callback.from_user.id)
    if not user or not user.is_registered:
        await callback.message.answer("Сначала зарегистрируйтесь — нажмите /start")
        return

    from bot.keyboards.user_kb import back_kb as _back_kb
    ticket = await dal.get_open_ticket(session, user.id)
    if not ticket:
        ticket = await dal.create_ticket(session, user.id)

    await state.set_state(__import__("bot.states.states", fromlist=["SupportSG"]).SupportSG.waiting_message)
    await state.update_data(ticket_id=ticket.id)
    await callback.message.answer(
        f"💬 <b>Поддержка</b>\n\nТикет #{ticket.id} открыт.\n"
        f"Опишите проблему — мы ответим как можно скорее.\n\n"
        f"Чтобы закрыть диалог — отправьте /close",
        parse_mode="HTML",
        reply_markup=_back_kb("back_main"),
    )
