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

# ── Инбаунды и хосты ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_inbounds")
async def admin_inbounds(callback: CallbackQuery):
    inbounds = await remnawave.get_inbounds()
    if not inbounds:
        await callback.answer("Инбаунды не найдены", show_alert=True)
        return
    lines = [
        f"{'✅' if ib.is_enabled else '❌'} <b>{ib.tag}</b> — {ib.type}\n<code>{ib.uuid}</code>"
        for ib in inbounds
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Хосты", callback_data="admin_hosts")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_cat_infra")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])
    await edit_or_answer(callback, f"🔌 <b>Инбаунды ({len(inbounds)})</b>\n\n" + "\n\n".join(lines), reply_markup=kb)


@router.callback_query(F.data == "admin_hosts")
async def admin_hosts(callback: CallbackQuery):
    hosts = await remnawave.get_hosts()
    if not hosts:
        await callback.answer("Хосты не найдены", show_alert=True)
        return
    lines = [
        f"{'✅' if h.is_enabled else '❌'} <b>{h.remark}</b>\n{h.address}:{h.port}"
        for h in hosts
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔌 Инбаунды", callback_data="admin_inbounds")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_cat_infra")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])
    await edit_or_answer(callback, f"🌐 <b>Хосты ({len(hosts)})</b>\n\n" + "\n\n".join(lines), reply_markup=kb)
