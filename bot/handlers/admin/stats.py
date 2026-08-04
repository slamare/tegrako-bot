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
from bot.handlers.admin._shared import admin_nav_kb

logger = logging.getLogger(__name__)

router = Router()

# ── Статистика ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery, session: AsyncSession):
    users = await dal.count_users(session)
    revenue = await dal.get_revenue_stats(session)
    pending = await dal.get_pending_payments(session)
    ref_days = await dal.get_setting(session, "referral_days", "0")
    try:
        nodes = await remnawave.get_nodes()
        nodes_online = sum(1 for n in nodes if n.is_connected)
        panel_text = f"\n\n<b>Ноды:</b> {nodes_online}/{len(nodes)} онлайн"
    except Exception:
        panel_text = "\n\n⚠️ Не удалось получить данные панели"

    reset_at = await dal.get_setting(session, "revenue_reset_at", "")
    reset_note = f"\n<i>Выручка с {reset_at[:10]}</i>" if reset_at else ""
    text = (
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Всего: {users['total']} | Зарег.: {users['registered']} | Бан: {users['banned']}\n"
        f"⏳ Ожидают оплаты: {len(pending)}\n\n"
        f"<b>Выручка:</b>\n"
        f"📅 Неделя: {revenue['weekly']:.0f} ₽\n"
        f"📆 Месяц: {revenue['monthly']:.0f} ₽\n"
        f"💰 Всего: {revenue['total']:.0f} ₽{reset_note}\n\n"
        f"🎁 Бонус за реферала: <b>{ref_days} дн.</b>"
        f"{panel_text}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="️ Изменить бонус за реферала", callback_data="admin_set_ref_days")],
        [InlineKeyboardButton(text="🗑 Сбросить выручку", callback_data="admin_reset_revenue")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])
    await edit_or_answer(callback, text, reply_markup=kb)


@router.callback_query(F.data == "admin_reset_revenue")
async def admin_reset_revenue(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, сбросить", callback_data="admin_reset_revenue_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])
    await edit_or_answer(
        callback,
        "⚠️ <b>Сбросить статистику выручки?</b>\n\nСтатистика начнёт считаться заново с сегодняшнего дня.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "admin_reset_revenue_confirm")
async def admin_reset_revenue_confirm(callback: CallbackQuery, session: AsyncSession):
    await dal.set_setting(session, "revenue_reset_at", datetime.utcnow().isoformat())
    await callback.answer("✅ Выручка сброшена", show_alert=True)
    await admin_stats(callback, session)


@router.callback_query(F.data == "admin_set_ref_days")
async def admin_set_ref_days(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSG.set_referral_days)
    msg = await callback.message.answer(
        "Введите количество дней за каждого оплатившего реферала.\n\n"
        "Введите <b>0</b> чтобы отключить бонус.",
        parse_mode="HTML",
    )
    await state.update_data(bot_prompt_msg_id=msg.message_id)
    await callback.answer()


@router.message(AdminSG.set_referral_days, F.text)
async def save_referral_days(message: Message, session: AsyncSession, state: FSMContext):
    try:
        days = int(message.text.strip())
        assert days >= 0
    except Exception:
        await cleanup_fsm_interaction(message, state)
        msg = await message.answer("❌ Введите целое число >= 0:")
        await state.update_data(bot_prompt_msg_id=msg.message_id)
        return
    await dal.set_setting(session, "referral_days", str(days))
    await cleanup_fsm_interaction(message, state)
    await state.clear()
    msg = await message.answer(f"✅ Бонус за реферала: <b>{days} дн.</b>", parse_mode="HTML")
    asyncio.create_task(delete_later(message.bot, message.chat.id, msg.message_id, 30))


# ── Режимы доступа ────────────────────────────────────────────────────────────

ACCESS_MODE_LABELS = {
    "open": "🟢 Открытый доступ",
    "closed": "🔴 Полное ограничение",
    "invite_only": "📨 Только по приглашениям",
    "no_purchase": "🚫 Запрет покупок",
    "no_register": "🚫 Запрет регистрации",
}
ACCESS_MODE_DESC = {
    "open": "Бот работает в обычном режиме.",
    "closed": "Все пользователи получают сообщение о недоступности.",
    "invite_only": "Регистрация только по реферальной ссылке.",
    "no_purchase": "Покупки заблокированы, бот доступен.",
    "no_register": "Регистрация закрыта, существующие пользователи работают.",
}


@router.callback_query(F.data == "admin_access_mode")
async def admin_access_mode(callback: CallbackQuery, session: AsyncSession):
    current = await dal.get_setting(session, "access_mode", "open")
    label = ACCESS_MODE_LABELS.get(current, current)
    desc = ACCESS_MODE_DESC.get(current, "")
    await edit_or_answer(
        callback,
        f"🔐 <b>Режим доступа</b>\n\nТекущий: <b>{label}</b>\n<i>{desc}</i>\n\nВыберите новый режим:",
        reply_markup=access_mode_kb(current),
    )


@router.callback_query(F.data.startswith("set_access_mode:"))
async def set_access_mode(callback: CallbackQuery, session: AsyncSession):
    mode = callback.data.split(":", 1)[1]
    if mode not in ACCESS_MODE_LABELS:
        await callback.answer("Неизвестный режим", show_alert=True)
        return
    await dal.set_setting(session, "access_mode", mode)
    await callback.answer(f"✅ {ACCESS_MODE_LABELS[mode]}", show_alert=True)
    await admin_access_mode(callback, session)
