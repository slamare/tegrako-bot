from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
)
from sqlalchemy.ext.asyncio import AsyncSession
import re

from bot.states.states import RegistrationSG
from bot.keyboards.user_kb import main_menu_kb, back_kb, profile_kb
from bot.services import remnawave
from config.settings import settings
from db import dal

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    tg_id = message.from_user.id

    maintenance = await dal.get_setting(session, "maintenance", "0")
    if maintenance == "1" and tg_id not in settings.admin_ids:
        await message.answer("🔧 Ведутся технические работы. Пожалуйста, попробуйте позже.")
        return

    # Парсим реферальный параметр: /start ref_123456
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
        user = await dal.create_user(session, tg_id, username=message.from_user.username,
                                     referred_by=referred_by)
    elif referred_by and not user.referred_by:
        await dal.update_user(session, tg_id, referred_by=referred_by)

    if message.from_user.username and user.username != message.from_user.username:
        await dal.update_user(session, tg_id, username=message.from_user.username)

    welcome_text = (
        f"👋 Добро пожаловать в <b>{settings.BOT_NAME}</b>!\n\n"
        f"Надёжный VPN с быстрыми серверами.\n"
        f"Выберите действие в меню ниже."
    )
    if settings.WELCOME_IMAGE_URL:
        try:
            img = settings.WELCOME_IMAGE_URL
            photo = img if img.startswith("http") else __import__('aiogram.types', fromlist=['FSInputFile']).FSInputFile(img)
            await message.answer_photo(photo, caption=welcome_text, parse_mode="HTML", reply_markup=main_menu_kb())
        except Exception:
            await message.answer(welcome_text, parse_mode="HTML", reply_markup=main_menu_kb())
    else:
        await message.answer(welcome_text, parse_mode="HTML", reply_markup=main_menu_kb())

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
            f"✅ Ваш аккаунт найден: <code>{rw_user.username}</code>.\nДобро пожаловать обратно!",
            parse_mode="HTML", reply_markup=main_menu_kb(),
        )
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
            f"⚠️ Имя <code>@{tg_username}</code> уже занято в системе.\n\nВведите другое имя (только латиница, цифры, _):",
            parse_mode="HTML", reply_markup=back_kb("cancel"),
        )
    else:
        await message.answer(
            "👤 У вас не установлен username в Telegram.\n\nПридумайте имя для аккаунта (только латиница, цифры, _):",
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
        f"✅ Аккаунт зарегистрирован: <code>{username}</code>.\n\nТеперь можете купить подписку.",
        parse_mode="HTML", reply_markup=main_menu_kb(),
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
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                days_left = (rw.expire_at - now).days
                expire_str = rw.expire_at.strftime("%d.%m.%Y")
                used_gb = round(rw.user_traffic.used_traffic_bytes / 1024 ** 3, 2)
                limit_gb = round(rw.traffic_limit_bytes / 1024 ** 3, 1) if rw.traffic_limit_bytes else "∞"
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
    ])
    await callback.message.edit_text(
        f"📋 <b>Ваша подписка</b>\n\n"
        f"Нажмите кнопку ниже чтобы открыть ссылку подключения в браузере.\n\n"
        f"⚠️ <b>Сброс ссылки</b> — сгенерирует новую ссылку. Старая перестанет работать.",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data == "revoke_subscription")
async def revoke_subscription(callback: CallbackQuery, session: AsyncSession):
    user = await dal.get_user(session, callback.from_user.id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, сбросить", callback_data="revoke_subscription_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="my_subscription")],
    ])
    await callback.message.edit_text(
        "⚠️ <b>Подтвердите сброс ссылки</b>\n\n"
        "Старая ссылка подписки перестанет работать.\nНужно будет обновить её во всех приложениях.",
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

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    if not devices:
        text = f"📱 <b>Мои устройства</b>\n\nУстройств не зарегистрировано.\nЛимит: {'∞' if not limit else limit} уст."
        builder = InlineKeyboardBuilder()
        builder.button(text="◀️ Назад", callback_data="back_profile")
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
        return

    text = f"📱 <b>Мои устройства</b> ({len(devices)}/{('∞' if not limit else limit)})\n\n"
    builder = InlineKeyboardBuilder()

    for i, d in enumerate(devices, 1):
        platform = d.platform or "Неизвестно"
        model = d.device_model or "—"
        text += f"{i}. {platform} — {model}\n"
        builder.button(text=f"🗑 Удалить {i}. {platform}", callback_data=f"delete_device:{d.hwid}")

    builder.button(text="🗑 Удалить все устройства", callback_data="delete_all_devices")
    builder.button(text="◀️ Назад", callback_data="back_profile")
    builder.adjust(1)

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())


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
async def delete_all_devices_confirm(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить все", callback_data="delete_all_devices_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="my_devices")],
    ])
    await callback.message.edit_text(
        "⚠️ <b>Удалить все устройства?</b>\n\nПосле этого нужно будет заново авторизоваться на всех устройствах.",
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
            "✅ <b>Все устройства удалены.</b>\n\nПри следующем подключении устройство добавится автоматически.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_profile")]
            ])
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
            lines.append(f"{s_emoji.get(p.status,'❓')} {date_str} — {int(p.amount)} ₽ ({p.tariff.name if p.tariff else '?'})")
        text = "\n".join(lines)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_kb("back_profile"))


# ── Реферальная система ───────────────────────────────────────────────────────

@router.message(F.text == "👥 Пригласить друга")
async def invite_friend(message: Message, session: AsyncSession):
    """Кнопка в меню — показывает ссылку и статистику пользователю в личке."""
    tg_id = message.from_user.id
    bot_info = await message.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{tg_id}"

    ref_days = int(await dal.get_setting(session, "referral_days", "0"))
    ref_count = await dal.count_referrals(session, tg_id)
    ref_paid = await dal.get_referrals_with_payment(session, tg_id)

    bonus_text = f"\n🎁 За каждого оплатившего друга вы получаете <b>+{ref_days} дней</b>." if ref_days else ""

    await message.answer(
        f"👥 <b>Реферальная программа</b>\n\n"
        f"Поделитесь ссылкой с друзьями:\n"
        f'<a href="{link}">{link}</a>'
        f"{bonus_text}\n\n"
        f"📊 Приглашено: {ref_count} | Оплатили: {len(ref_paid)}",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ── Inline-режим: @tegrakobot invite ─────────────────────────────────────────

@router.inline_query(F.query.lower() == "invite")
async def inline_invite(inline_query: InlineQuery):
    """@tegrakobot invite — возвращает баннер для вставки в любой чат."""
    tg_id = inline_query.from_user.id
    bot_info = await inline_query.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{tg_id}"

    text = (
        f"🚀 Привет! Хочешь стабильный и быстрый VPN?\n\n"
        f"{settings.BOT_NAME} — поможет тебе с этим!\n\n"
        f"📌 Жми кнопку и попробуй бесплатно!"
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
async def back_to_main(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()

@router.callback_query(F.data == "back_profile")
async def back_to_profile(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()

@router.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.answer("Отменено")
