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

# ── Тикеты ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_tickets")
async def admin_tickets(callback: CallbackQuery, session: AsyncSession):
    tickets = await dal.get_open_tickets(session)
    builder = InlineKeyboardBuilder()
    for t in tickets:
        builder.button(
            text=f"#{t.id} — @{t.user.username or t.user.telegram_id}",
            callback_data=f"view_ticket:{t.id}",
        )
    builder.button(text="📁 Закрытые тикеты", callback_data="admin_closed_tickets")
    builder.button(text="◀️ Назад", callback_data="admin_cat_users")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)
    header = f"🎫 <b>Открытые тикеты: {len(tickets)}</b>" if tickets else "✅ Открытых тикетов нет."
    await edit_or_answer(callback, header, reply_markup=builder.as_markup())


@router.callback_query(F.data == "admin_closed_tickets")
async def admin_closed_tickets(callback: CallbackQuery, session: AsyncSession):
    tickets = await dal.get_closed_tickets(session, limit=20)
    builder = InlineKeyboardBuilder()
    for t in tickets:
        date_str = t.updated_at.strftime("%d.%m")
        builder.button(
            text=f"#{t.id} {date_str} — @{t.user.username or t.user.telegram_id}",
            callback_data=f"view_ticket:{t.id}",
        )
    builder.button(text="◀️ Назад", callback_data="admin_tickets")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)
    await edit_or_answer(
        callback,
        f"📁 <b>Закрытые тикеты (последние {len(tickets)})</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("view_ticket:"))
async def view_ticket(callback: CallbackQuery, session: AsyncSession):
    ticket_id = int(callback.data.split(":")[1])
    ticket = await dal.get_ticket_by_id(session, ticket_id)
    if not ticket:
        await callback.answer("Тикет не найден", show_alert=True)
        return
    u = ticket.user
    history = "\n".join(
        f"{'👤' if m.sender_role == 'user' else '🛡'} {m.text or f'[{m.media_type}]'}"
        for m in ticket.messages[-10:]
    )
    status_icon = "🟢" if ticket.status == "open" else "🔴"
    await edit_or_answer(
        callback,
        f"🎫 <b>Тикет #{ticket_id}</b> {status_icon}\n"
        f"👤 @{u.username or '—'} (<code>{u.telegram_id}</code>)\n"
        f"Аккаунт: <code>{u.remnawave_username or '—'}</code>\n\n"
        f"<b>Последние сообщения:</b>\n{history or 'нет'}",
        reply_markup=ticket_reply_kb(ticket_id, is_closed=ticket.status == "closed"),
    )


@router.callback_query(F.data.startswith("reply_ticket:"))
async def reply_ticket_start(callback: CallbackQuery, state: FSMContext):
    ticket_id = int(callback.data.split(":")[1])
    await state.set_state(AdminSG.replying_ticket)
    await state.update_data(ticket_id=ticket_id)
    msg = await callback.message.answer(f"✏️ Введите ответ на тикет #{ticket_id}:")
    await state.update_data(bot_prompt_msg_id=msg.message_id)
    await callback.answer()


@router.message(AdminSG.replying_ticket, F.text)
async def send_ticket_reply(message: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    ticket = await dal.get_ticket_by_id(session, data.get("ticket_id"))
    if not ticket:
        await state.clear()
        return
    await dal.add_ticket_message(
        session, ticket_id=ticket.id, sender_role="admin",
        sender_tg_id=message.from_user.id, text=message.text,
    )
    await cleanup_fsm_interaction(message, state)
    try:
        await message.bot.send_message(
            ticket.user.telegram_id,
            f"💬 <b>Ответ поддержки (Тикет #{ticket.id}):</b>\n\n{message.text}",
            disable_notification=True, parse_mode="HTML",
        )
        msg = await message.answer("✅ Ответ отправлен.")
    except Exception:
        msg = await message.answer("⚠️ Не удалось доставить сообщение.")
    await state.clear()
    asyncio.create_task(delete_later(message.bot, message.chat.id, msg.message_id, 30))


@router.callback_query(F.data.startswith("close_ticket:"))
async def close_ticket(callback: CallbackQuery, session: AsyncSession):
    ticket_id = int(callback.data.split(":")[1])
    ticket = await dal.get_ticket_by_id(session, ticket_id)
    if not ticket:
        await callback.answer("Не найден", show_alert=True)
        return
    await dal.close_ticket(session, ticket_id)
    try:
        await callback.bot.send_message(
            ticket.user.telegram_id,
            f"✅ Тикет #{ticket_id} закрыт. Если вопрос остался — создайте новый.",
            disable_notification=True,
        )
    except Exception:
        pass
    try:
        suffix = "\n\n🔒 <b>ЗАКРЫТ</b>"
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=(callback.message.caption or "") + suffix, parse_mode="HTML",
                reply_markup=ticket_reply_kb(ticket_id, is_closed=True),
            )
        else:
            await callback.message.edit_text(
                (callback.message.text or "") + suffix, parse_mode="HTML",
                reply_markup=ticket_reply_kb(ticket_id, is_closed=True),
            )
    except Exception:
        pass
    await callback.answer("Закрыт")
