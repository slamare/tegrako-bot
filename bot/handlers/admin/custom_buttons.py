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

# ── Кастомные кнопки меню ─────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_custom_buttons")
async def admin_custom_buttons(callback: CallbackQuery, session: AsyncSession):
    buttons = await dal.get_all_custom_buttons(session)
    builder = InlineKeyboardBuilder()
    for btn in buttons:
        builder.button(
            text=f"{'✅' if btn.is_active else '❌'} {btn.text}",
            callback_data=f"admin_custbtn:{btn.id}",
        )
    builder.button(text="➕ Добавить кнопку", callback_data="admin_add_custbtn")
    builder.button(text="◀️ Назад", callback_data="admin_menu")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)
    await edit_or_answer(
        callback,
        f"🔘 <b>Кастомные кнопки ({len(buttons)})</b>\n\nКнопки показываются пользователям как inline-блок после приветствия.",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "admin_add_custbtn")
async def admin_add_custbtn(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSG.custbtn_text)
    msg = await callback.message.answer("Введите <b>текст кнопки</b>:", parse_mode="HTML")
    await state.update_data(bot_prompt_msg_id=msg.message_id)
    await callback.answer()


@router.message(AdminSG.custbtn_text, F.text)
async def custbtn_text(message: Message, state: FSMContext):
    await cleanup_fsm_interaction(message, state)
    await state.update_data(btn_text=message.text.strip())
    await state.set_state(AdminSG.custbtn_url)
    msg = await message.answer("Введите <b>URL</b> (можно tg://...):", parse_mode="HTML")
    await state.update_data(bot_prompt_msg_id=msg.message_id)


@router.message(AdminSG.custbtn_url, F.text)
async def custbtn_url(message: Message, state: FSMContext):
    await cleanup_fsm_interaction(message, state)
    await state.update_data(btn_url=message.text.strip())
    await state.set_state(AdminSG.custbtn_condition)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Всем", callback_data="custbtn_cond:all")],
        [InlineKeyboardButton(text="✅ Только с активной подпиской", callback_data="custbtn_cond:active_sub")],
    ])
    msg = await message.answer("Кому показывать кнопку?", reply_markup=kb)
    await state.update_data(bot_prompt_msg_id=msg.message_id)


@router.callback_query(F.data.startswith("custbtn_cond:"))
async def custbtn_condition(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    btn = await dal.create_custom_button(session, text=data["btn_text"], url=data["btn_url"], condition=callback.data.split(":")[1])
    await edit_or_answer(callback, f"✅ Кнопка <b>{btn.text}</b> добавлена.", reply_markup=admin_nav_kb("admin_custom_buttons"))


@router.callback_query(F.data.startswith("admin_custbtn:"))
async def view_custbtn(callback: CallbackQuery, session: AsyncSession):
    btn_id = int(callback.data.split(":")[1])
    from db.models import CustomMenuButton
    btn = await session.get(CustomMenuButton, btn_id)
    if not btn:
        await callback.answer("Не найдено", show_alert=True)
        return
    condition_label = "Всем" if btn.condition == "all" else "Только с подпиской"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="❌ Скрыть" if btn.is_active else "✅ Показать",
            callback_data=f"toggle_custbtn:{btn_id}",
        )],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_custbtn:{btn_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_custom_buttons")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])
    await edit_or_answer(
        callback,
        f"🔘 <b>{btn.text}</b>\nURL: <code>{btn.url}</code>\nПоказывать: {condition_label}\nСтатус: {'✅ Активна' if btn.is_active else '❌ Скрыта'}",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("toggle_custbtn:"))
async def toggle_custbtn(callback: CallbackQuery, session: AsyncSession):
    btn_id = int(callback.data.split(":")[1])
    from db.models import CustomMenuButton
    btn = await session.get(CustomMenuButton, btn_id)
    if not btn:
        await callback.answer("Не найдено", show_alert=True)
        return
    await dal.update_custom_button(session, btn_id, is_active=not btn.is_active)
    await callback.answer("Статус обновлён")
    await view_custbtn(callback, session)


@router.callback_query(F.data.startswith("delete_custbtn:"))
async def delete_custbtn(callback: CallbackQuery, session: AsyncSession):
    await dal.delete_custom_button(session, int(callback.data.split(":")[1]))
    await callback.answer("Удалено")
    await admin_custom_buttons(callback, session)
