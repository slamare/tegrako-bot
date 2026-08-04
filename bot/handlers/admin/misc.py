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

# ── FSM: нежелательные типы сообщений ────────────────────────────────────────

@router.message(F.voice | F.video_note | F.sticker)
async def catch_voice_in_fsm(message: Message, state: FSMContext):
    if not await state.get_state():
        return
    try:
        await message.delete()
    except Exception:
        pass
    hint = "🎙 Голосовые и кружки не принимаются." if (message.voice or message.video_note) else "📎 Стикеры не принимаются."
    msg = await message.answer(hint, disable_notification=True)
    asyncio.create_task(delete_later(message.bot, message.chat.id, msg.message_id, 30))


@router.message(F.photo | F.video | F.animation | F.document | F.contact | F.location)
async def catch_media_in_fsm(message: Message, state: FSMContext):
    current = await state.get_state()
    if not current or current == AdminSG.broadcast_text:
        return
    try:
        await message.delete()
    except Exception:
        pass
    msg = await message.answer("📎 Здесь ожидается текстовый ввод.", disable_notification=True)
    asyncio.create_task(delete_later(message.bot, message.chat.id, msg.message_id, 30))


@router.callback_query(F.data == "notify_dismiss")
async def notify_dismiss(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("torrent_warn_user:"))
async def torrent_warn_user(callback: CallbackQuery):
    tg_id = int(callback.data.split(":")[1])
    try:
        await callback.bot.send_message(
            tg_id,
            "🏴\u200d☠️ <b>Предупреждение от администратора</b>\n\n"
            "Зафиксирована загрузка торрентов через VPN.\n"
            "Это нарушает правила сервиса.\n\n"
            "При повторных нарушениях подписка будет заблокирована без возврата средств.",
            parse_mode="HTML",
        )
        await callback.answer("✅ Сообщение отправлено")
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {str(e)[:60]}", show_alert=True)
