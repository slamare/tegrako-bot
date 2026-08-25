from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
import asyncio
import logging

from bot.states.states import AdminSG
from bot.keyboards.admin_kb import (
    admin_menu_kb, payment_approve_kb, ticket_reply_kb,
    tariff_list_kb, tariff_manage_kb, nodes_kb, node_manage_kb,
    user_manage_kb, broadcast_target_kb, promo_list_kb, access_mode_kb,
)
from bot.keyboards.user_kb import main_menu_kb
from bot.services import remnawave
from bot.utils.helpers import edit_or_answer, cleanup_fsm_interaction, delete_later
from config.settings import settings
from db import dal
from db.models import Payment
from bot.handlers.admin._shared import admin_nav_kb, _provision_mtproto

logger = logging.getLogger(__name__)

router = Router()

# ── Пользователи ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery, session: AsyncSession):
    users = await dal.get_all_users(session, only_registered=True)
    builder = InlineKeyboardBuilder()
    for u in users[:20]:
        builder.button(
            text=f"@{u.username or '—'} | {u.remnawave_username or '?'}",
            callback_data=f"admin_user:{u.telegram_id}",
        )
    builder.button(text="🔍 Поиск", callback_data="admin_search_user")
    builder.button(text="🚫 Забаненные", callback_data="admin_banned_users")
    builder.button(text="◀️ Назад", callback_data="admin_cat_users")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)
    await edit_or_answer(callback, f"👥 <b>Пользователи ({len(users)})</b>", reply_markup=builder.as_markup())


@router.callback_query(F.data == "admin_search_user")
async def admin_search_user_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSG.search_user)
    msg = await callback.message.answer("Введите username, имя аккаунта или Telegram ID:")
    await state.update_data(bot_prompt_msg_id=msg.message_id)
    await callback.answer()


@router.message(AdminSG.search_user, F.text)
async def admin_search_user(message: Message, session: AsyncSession, state: FSMContext):
    await cleanup_fsm_interaction(message, state)
    users = await dal.search_users(session, message.text.strip())
    if not users:
        msg = await message.answer("Ничего не найдено.")
        asyncio.create_task(delete_later(message.bot, message.chat.id, msg.message_id, 30))
        return
    builder = InlineKeyboardBuilder()
    for u in users:
        builder.button(
            text=f"@{u.username or '—'} | {u.remnawave_username or '?'}",
            callback_data=f"admin_user:{u.telegram_id}",
        )
    builder.adjust(1)
    msg = await message.answer(f"🔍 Найдено: {len(users)}", reply_markup=builder.as_markup())
    await state.clear()
    asyncio.create_task(delete_later(message.bot, message.chat.id, msg.message_id, 30))


@router.callback_query(F.data == "admin_banned_users")
async def admin_banned_users(callback: CallbackQuery, session: AsyncSession):
    users = await dal.get_banned_users(session)
    builder = InlineKeyboardBuilder()
    for u in users:
        builder.button(text=f"🚫 @{u.username or u.telegram_id}", callback_data=f"admin_user:{u.telegram_id}")
    builder.button(text="◀️ Назад", callback_data="admin_users")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)
    header = f"🚫 <b>Забаненные ({len(users)})</b>" if users else "✅ Забаненных нет."
    await edit_or_answer(callback, header, reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("admin_user:"))
async def view_user(callback: CallbackQuery, session: AsyncSession):
    tg_id = int(callback.data.split(":")[1])
    user = await dal.get_user(session, tg_id)
    if not user:
        await callback.answer("Не найден", show_alert=True)
        return

    status_emoji = "⚪"
    sub_info = "Нет подписки"
    devices_info = ""
    if user.remnawave_uuid:
        try:
            rw = await remnawave.get_subscription_info(user.remnawave_uuid)
            if rw:
                status_emoji = {"ACTIVE": "🟢", "EXPIRED": "🔴", "DISABLED": "⚫"}.get(rw.status.value, "⚪")
                sub_info = f"{rw.status.value} до {rw.expire_at.strftime('%d.%m.%Y')}"
                devices = await remnawave.get_user_devices(user.remnawave_uuid)
                limit_str = str(rw.hwid_device_limit) if rw.hwid_device_limit else "∞"
                devices_info = f"\n📱 Устройства: {len(devices)} / {limit_str}"
        except Exception:
            sub_info = "⚠️ не удалось получить данные"

    payments_count = await dal.count_user_payments(session, user.id)
    ref_count = await dal.count_referrals(session, tg_id)
    ref_paid = await dal.get_referrals_with_payment(session, tg_id)
    referrer_info = f"\nПривёл: <code>{user.referred_by}</code>" if user.referred_by else ""
    slots_info = f"\n➕ Доп. слоты: {user.extra_device_slots}" if user.extra_device_slots else ""
    role_icon = {"developer": "👨‍💻", "admin": "⚙️", "user": "👤"}.get(user.role, "👤")

    await edit_or_answer(
        callback,
        f"{role_icon} <b>Пользователь</b>\n"
        f"@{user.username or '—'} | ID: <code>{tg_id}</code>\n"
        f"Аккаунт: <code>{user.remnawave_username or '—'}</code>\n\n"
        f"{status_emoji} Подписка: {sub_info}"
        f"{devices_info}\n"
        f"💰 Платежей: {payments_count}\n"
        f"👥 Рефералов: {ref_count} (оплатили: {len(ref_paid)})\n"
        f"Забанен: {'🚫 Да' if user.is_banned else '✅ Нет'}"
        f"{slots_info}"
        f"{referrer_info}\n"
        f"С: {user.created_at.strftime('%d.%m.%Y')}",
        reply_markup=user_manage_kb(tg_id, user.is_banned, user.remnawave_uuid),
    )


@router.callback_query(F.data.startswith("toggle_ban:"))
async def toggle_ban(callback: CallbackQuery, session: AsyncSession):
    tg_id = int(callback.data.split(":")[1])
    user = await dal.get_user(session, tg_id)
    if not user:
        await callback.answer("Не найден", show_alert=True)
        return
    new_ban = not user.is_banned
    await dal.update_user(session, tg_id, is_banned=new_ban)
    await callback.answer(f"{'🚫 Забанен' if new_ban else '✅ Разбанен'}", show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=user_manage_kb(tg_id, new_ban, user.remnawave_uuid))


@router.callback_query(F.data.startswith("admin_grant_unlimited:"))
async def admin_grant_unlimited(callback: CallbackQuery, session: AsyncSession):
    tg_id = int(callback.data.split(":")[1])
    user = await dal.get_user(session, tg_id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    ok = await remnawave.set_expire_at(user.remnawave_uuid, datetime(2099, 12, 31, 16, 59, 59, tzinfo=timezone.utc))
    if ok:
        remnawave.invalidate_sub_info_cache(user.remnawave_uuid)
        await callback.answer("✅ Бессрочный доступ до 31.12.2099", show_alert=True)
    else:
        await callback.answer("❌ Ошибка API", show_alert=True)


@router.callback_query(F.data.startswith("admin_assign_tariff:"))
async def admin_assign_tariff_start(callback: CallbackQuery, session: AsyncSession):
    tg_id = int(callback.data.split(":")[1])
    tariffs = await dal.get_active_tariffs(session)
    builder = InlineKeyboardBuilder()
    for t in tariffs:
        builder.button(text=f"{t.name} — {t.duration_days} дн.", callback_data=f"do_assign_tariff:{tg_id}:{t.id}")
    builder.button(text="◀️ Назад", callback_data=f"admin_user:{tg_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)
    await edit_or_answer(callback, "📦 Выберите тариф для назначения:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("do_assign_tariff:"))
async def do_assign_tariff(callback: CallbackQuery, session: AsyncSession):
    _, tg_id_str, tariff_id_str = callback.data.split(":")
    tg_id, tariff_id = int(tg_id_str), int(tariff_id_str)
    user = await dal.get_user(session, tg_id)
    tariff = await dal.get_tariff(session, tariff_id)
    if not user or not tariff:
        await callback.answer("Не найдено", show_alert=True)
        return
    if not user.remnawave_uuid and not user.remnawave_username:
        await callback.answer(
            "❌ У пользователя не задан аккаунт. Попросите пройти /start и завершить регистрацию.",
            show_alert=True,
        )
        return
    try:
        squad_uuid = settings.ADMIN_GRANT_SQUAD_UUID or tariff.squad_uuid or settings.DEFAULT_SQUAD_UUID
        if user.remnawave_uuid:
            await remnawave.extend_subscription(user.remnawave_uuid, tariff.duration_days)
            await remnawave.update_user_limits(
                user.remnawave_uuid,
                traffic_limit_gb=tariff.traffic_limit_gb,
                device_limit=tariff.device_limit,
            )
            if squad_uuid:
                await remnawave.add_user_to_squad(user.remnawave_uuid, squad_uuid)
        else:
            rw_user = await remnawave.create_user(
                username=user.remnawave_username,
                duration_days=tariff.duration_days,
                traffic_limit_gb=tariff.traffic_limit_gb,
                device_limit=tariff.device_limit,
                telegram_id=tg_id,
            )
            await dal.update_user(session, tg_id, remnawave_uuid=str(rw_user.uuid))
            if squad_uuid:
                await remnawave.add_user_to_squad(str(rw_user.uuid), squad_uuid)
        remnawave.invalidate_sub_info_cache(user.remnawave_uuid)
        fresh_user = await dal.get_user(session, tg_id)
        if fresh_user:
            await _provision_mtproto(session, fresh_user, tariff)
        await callback.answer(f"✅ Тариф {tariff.name} назначен", show_alert=True)
        try:
            await callback.bot.send_message(
                tg_id,
                f"✅ <b>Администратор активировал подписку!</b>\n\nТариф: {tariff.name} ({tariff.duration_days} дн.)",
                parse_mode="HTML",
            )
        except Exception:
            pass
    except Exception as e:
        await callback.answer(f"Ошибка: {str(e)[:100]}", show_alert=True)


@router.callback_query(F.data.startswith("admin_sub_manage:"))
async def admin_sub_manage(callback: CallbackQuery, session: AsyncSession):
    tg_id = int(callback.data.split(":")[1])
    user = await dal.get_user(session, tg_id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    rw = await remnawave.get_user_by_uuid(user.remnawave_uuid)
    if not rw:
        await callback.answer("Не удалось получить данные", show_alert=True)
        return
    now = datetime.now(timezone.utc)
    days_left = (rw.expire_at - now).days
    used_gb = round(rw.user_traffic.used_traffic_bytes / 1024 ** 3, 2)
    limit_gb = round(rw.traffic_limit_bytes / 1024 ** 3, 1) if rw.traffic_limit_bytes else "∞"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Ссылка подписки", url=rw.subscription_url)],
        [InlineKeyboardButton(text="🔄 Сброс трафика", callback_data=f"admin_reset_traffic:{tg_id}")],
        [InlineKeyboardButton(
            text="✅ Включить" if rw.status.value != "ACTIVE" else "⛔ Отключить",
            callback_data=f"admin_toggle_sub:{tg_id}",
        )],
        [InlineKeyboardButton(text="🗑 Удалить из панели", callback_data=f"admin_delete_sub:{tg_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_user:{tg_id}")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])
    await edit_or_answer(
        callback,
        f"📋 <b>Подписка пользователя</b>\n\n"
        f"Статус: {rw.status.value}\n"
        f"До: {rw.expire_at.strftime('%d.%m.%Y')} ({days_left} дн.)\n"
        f"Трафик: {used_gb} / {limit_gb} ГБ\n"
        f"Устройств: {rw.hwid_device_limit}",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("admin_reset_traffic:"))
async def admin_reset_traffic(callback: CallbackQuery, session: AsyncSession):
    tg_id = int(callback.data.split(":")[1])
    user = await dal.get_user(session, tg_id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    ok = await remnawave.reset_user_traffic(user.remnawave_uuid)
    await callback.answer("✅ Трафик сброшен" if ok else "❌ Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("admin_toggle_sub:"))
async def admin_toggle_sub(callback: CallbackQuery, session: AsyncSession):
    tg_id = int(callback.data.split(":")[1])
    user = await dal.get_user(session, tg_id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    rw = await remnawave.get_user_by_uuid(user.remnawave_uuid)
    if not rw:
        await callback.answer("Не удалось получить данные", show_alert=True)
        return
    new_status = "DISABLED" if rw.status.value == "ACTIVE" else "ACTIVE"
    await remnawave.set_user_status(user.remnawave_uuid, new_status)
    await callback.answer(
        "⛔ Подписка отключена" if new_status == "DISABLED" else "✅ Подписка включена",
        show_alert=True,
    )
    await admin_sub_manage(callback, session)


@router.callback_query(F.data.startswith("admin_delete_sub:"))
async def admin_delete_sub(callback: CallbackQuery, session: AsyncSession):
    tg_id = int(callback.data.split(":")[1])
    user = await dal.get_user(session, tg_id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"admin_delete_sub_confirm:{tg_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_sub_manage:{tg_id}")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])
    await edit_or_answer(
        callback,
        "⚠️ <b>Удалить пользователя из панели?</b>\n\nПодписка и все данные будут удалены из Remnawave.",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("admin_delete_sub_confirm:"))
async def admin_delete_sub_confirm(callback: CallbackQuery, session: AsyncSession):
    tg_id = int(callback.data.split(":")[1])
    user = await dal.get_user(session, tg_id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    ok = await remnawave.delete_panel_user(user.remnawave_uuid)
    if ok:
        await dal.update_user(session, tg_id, remnawave_uuid=None)
        await callback.answer("✅ Удалено из панели", show_alert=True)
        await view_user(callback, session)
    else:
        await callback.answer("❌ Ошибка удаления", show_alert=True)
