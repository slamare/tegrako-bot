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

# ── Тех. работы ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_toggle_maintenance")
async def toggle_maintenance(callback: CallbackQuery, session: AsyncSession):
    current = await dal.get_setting(session, "maintenance", "0")
    new_val = "0" if current == "1" else "1"
    await dal.set_setting(session, "maintenance", new_val)
    users = await dal.get_all_users(session, only_registered=True)
    sent = 0
    if new_val == "1":
        text = "🔧 <b>Технические работы</b>\n\nСервис временно недоступен. Приносим извинения!"
        alert = "🔴 Тех. работы начаты"
    else:
        text = "✅ <b>Технические работы завершены</b>\n\nСервис снова работает. Спасибо за терпение!"
        alert = "🟢 Тех. работы завершены"
    for u in users:
        if u.telegram_id in settings.admin_ids:
            continue
        try:
            await callback.bot.send_message(u.telegram_id, text, parse_mode="HTML")
            sent += 1
        except Exception:
            pass
    await callback.answer(f"{alert}. Уведомлено: {sent}", show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=admin_menu_kb(maintenance_on=new_val == "1"))
