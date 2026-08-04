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

# ── Рассылка ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: CallbackQuery):
    await edit_or_answer(callback, "📢 <b>Рассылка</b>\n\nВыберите аудиторию:", reply_markup=broadcast_target_kb())


@router.callback_query(F.data.startswith("broadcast:"))
async def broadcast_target_cb(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSG.broadcast_text)
    await state.update_data(broadcast_target=callback.data.split(":")[1])
    msg = await callback.message.answer("✏️ Введите текст рассылки (поддерживается HTML):")
    await state.update_data(bot_prompt_msg_id=msg.message_id)
    await callback.answer()


@router.message(AdminSG.broadcast_text, F.text)
async def send_broadcast(message: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    target = data.get("broadcast_target", "all")
    users = await dal.get_all_users(session, only_registered=True)

    panel_by_uuid: dict = {}
    if target in ("active", "expired"):
        panel_by_uuid = {u.uuid: u for u in await remnawave.get_all_users_bulk()}

    targets = []
    for u in users:
        if u.telegram_id in settings.admin_ids:
            continue
        if target in ("active", "expired") and u.remnawave_uuid:
            rw = panel_by_uuid.get(u.remnawave_uuid)
            status = rw.status.value if rw else ""
            if target == "active" and status != "ACTIVE":
                continue
            if target == "expired" and status == "ACTIVE":
                continue
        targets.append(u)

    sent = failed = 0
    sem = asyncio.Semaphore(25)

    async def _send(u):
        nonlocal sent, failed
        async with sem:
            try:
                await message.bot.send_message(u.telegram_id, message.text, parse_mode="HTML")
                sent += 1
            except Exception:
                failed += 1

    await asyncio.gather(*[_send(u) for u in targets])
    await state.clear()
    await message.answer(f"📢 Готово. ✅ {sent} | ❌ {failed}")
