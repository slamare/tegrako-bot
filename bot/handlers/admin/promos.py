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

# ── Промокоды ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_promos")
async def admin_promos(callback: CallbackQuery, session: AsyncSession):
    promos = await dal.get_all_promos(session)
    await edit_or_answer(callback, f"🎟 <b>Промокоды ({len(promos)})</b>", reply_markup=promo_list_kb(promos))


@router.callback_query(F.data == "admin_create_promo")
async def create_promo_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSG.promo_code)
    msg = await callback.message.answer(
        "🎟 Создание промокода\n\nВведите <b>код</b> (латиница, цифры, без пробелов):",
        parse_mode="HTML",
    )
    await state.update_data(bot_prompt_msg_id=msg.message_id)
    await callback.answer()


@router.message(AdminSG.promo_code, F.text)
async def promo_code(message: Message, state: FSMContext):
    code = message.text.strip().upper()
    if not code.replace("_", "").replace("-", "").isalnum():
        await cleanup_fsm_interaction(message, state)
        msg = await message.answer("❌ Только латиница, цифры, дефис и подчёркивание:")
        await state.update_data(bot_prompt_msg_id=msg.message_id)
        return
    await cleanup_fsm_interaction(message, state)
    await state.update_data(code=code)
    await state.set_state(AdminSG.promo_discount)
    msg = await message.answer(
        "Введите скидку:\n• Процент: <b>20%</b>\n• Фиксированная сумма: <b>100</b>",
        parse_mode="HTML",
    )
    await state.update_data(bot_prompt_msg_id=msg.message_id)


@router.message(AdminSG.promo_discount, F.text)
async def promo_discount(message: Message, state: FSMContext):
    text = message.text.strip()
    if text.endswith("%"):
        try:
            pct = int(text[:-1])
            assert 1 <= pct <= 100
            await state.update_data(discount_percent=pct, discount_fixed=0)
        except Exception:
            await cleanup_fsm_interaction(message, state)
            msg = await message.answer("❌ Процент от 1 до 100:")
            await state.update_data(bot_prompt_msg_id=msg.message_id)
            return
    else:
        try:
            fixed = float(text.replace(",", "."))
            assert fixed > 0
            await state.update_data(discount_percent=0, discount_fixed=fixed)
        except Exception:
            await cleanup_fsm_interaction(message, state)
            msg = await message.answer("❌ Введите число или процент (например 20%):")
            await state.update_data(bot_prompt_msg_id=msg.message_id)
            return
    await cleanup_fsm_interaction(message, state)
    await state.set_state(AdminSG.promo_max_uses)
    msg = await message.answer("Введите <b>максимальное количество использований</b>:", parse_mode="HTML")
    await state.update_data(bot_prompt_msg_id=msg.message_id)


@router.message(AdminSG.promo_max_uses, F.text)
async def promo_max_uses(message: Message, session: AsyncSession, state: FSMContext):
    try:
        uses = int(message.text.strip())
        assert uses > 0
    except Exception:
        await cleanup_fsm_interaction(message, state)
        msg = await message.answer("❌ Введите целое число > 0:")
        await state.update_data(bot_prompt_msg_id=msg.message_id)
        return
    data = await state.get_data()
    data["max_uses"] = uses
    promo = await dal.create_promo(session, **data)
    await state.clear()
    await cleanup_fsm_interaction(message, state)
    disc = f"{promo.discount_percent}%" if promo.discount_percent else f"{int(promo.discount_fixed)} ₽"
    msg = await message.answer(
        f"✅ Промокод <b>{promo.code}</b> создан!\nСкидка: {disc} | Использований: 0/{uses}",
        parse_mode="HTML",
    )
    asyncio.create_task(delete_later(message.bot, message.chat.id, msg.message_id, 30))


@router.callback_query(F.data.startswith("admin_promo:"))
async def view_promo(callback: CallbackQuery, session: AsyncSession):
    promo_id = int(callback.data.split(":")[1])
    from db.models import PromoCode
    promo = await session.get(PromoCode, promo_id)
    if not promo:
        await callback.answer("Не найден", show_alert=True)
        return
    disc = f"{promo.discount_percent}%" if promo.discount_percent else f"{int(promo.discount_fixed)} ₽"
    expires = promo.expires_at.strftime("%d.%m.%Y") if promo.expires_at else "бессрочно"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="❌ Деактивировать" if promo.is_active else "✅ Активировать",
            callback_data=f"toggle_promo:{promo_id}",
        )],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_promo:{promo_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_promos")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])
    await edit_or_answer(
        callback,
        f"🎟 <b>{promo.code}</b>\n"
        f"Скидка: {disc}\n"
        f"Использований: {promo.used_count}/{promo.max_uses}\n"
        f"Действует до: {expires}\n"
        f"Статус: {'✅ Активен' if promo.is_active else '❌ Неактивен'}",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("toggle_promo:"))
async def toggle_promo(callback: CallbackQuery, session: AsyncSession):
    promo_id = int(callback.data.split(":")[1])
    from db.models import PromoCode
    promo = await session.get(PromoCode, promo_id)
    if not promo:
        await callback.answer("Не найден", show_alert=True)
        return
    await dal.update_promo(session, promo_id, is_active=not promo.is_active)
    await callback.answer("Статус обновлён")
    await view_promo(callback, session)


@router.callback_query(F.data.startswith("delete_promo:"))
async def delete_promo(callback: CallbackQuery, session: AsyncSession):
    promo_id = int(callback.data.split(":")[1])
    await session.execute(update(Payment).where(Payment.promo_id == promo_id).values(promo_id=None))
    await session.flush()
    await dal.delete_promo(session, promo_id)
    await callback.answer("✅ Промокод удалён")
    await admin_promos(callback, session)
