from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
import re

from bot.states.states import RegistrationSG
from bot.keyboards.user_kb import main_menu_kb, back_kb, profile_kb, subscription_kb
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

    user = await dal.get_user(session, tg_id)
    if not user:
        user = await dal.create_user(session, tg_id, username=message.from_user.username)

    if message.from_user.username and user.username != message.from_user.username:
        await dal.update_user(session, tg_id, username=message.from_user.username)

    welcome_text = (
        f"👋 Добро пожаловать в <b>{settings.BOT_NAME}</b>!\n\n"
        f"Надёжный VPN с быстрыми серверами.\n"
        f"Выберите действие в меню ниже."
    )
    if settings.WELCOME_IMAGE_URL:
        try:
            from aiogram.types import FSInputFile
            img = settings.WELCOME_IMAGE_URL
            photo = img if img.startswith("http") else FSInputFile(img)
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

    await message.answer(
        f"👤 <b>Личный кабинет</b>\n\n"
        f"🆔 ID: <code>{tg_id}</code>\n"
        f"👤 Аккаунт: <code>{user.remnawave_username}</code>"
        f"{sub_info}",
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
    await callback.message.edit_text(
        f"📋 <b>Ваша подписка</b>\n\n"
        f"🔗 Ссылка для подключения:\n<code>{rw.subscription_url}</code>\n\n"
        f"Скопируйте ссылку и вставьте в VPN-клиент.",
        parse_mode="HTML",
        reply_markup=subscription_kb(),
    )


@router.callback_query(F.data == "my_devices")
async def my_devices(callback: CallbackQuery, session: AsyncSession):
    user = await dal.get_user(session, callback.from_user.id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    rw = await remnawave.get_subscription_info(user.remnawave_uuid)
    if not rw or not rw.hwid_device_limit:
        text = "📱 <b>Мои устройства</b>\n\nДанные недоступны."
    else:
        text = f"📱 <b>Мои устройства</b>\n\nЛимит: {rw.hwid_device_limit} устройств."
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_kb("back_profile"))


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

@router.message(F.text == "👥 Пригласить друга")
async def invite_friend(message: Message):
    bot_info = await message.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{message.from_user.id}"
    await message.answer(
        f"👥 <b>Пригласите друга!</b>\n\nПоделитесь ссылкой:\n<code>{link}</code>",
        parse_mode="HTML",
    )
