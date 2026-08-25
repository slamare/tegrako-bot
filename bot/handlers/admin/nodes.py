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

# ── Ноды ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_nodes")
async def admin_nodes(callback: CallbackQuery):
    nodes = await remnawave.get_nodes()
    if not nodes:
        await callback.answer("Ноды не найдены", show_alert=True)
        return
    await edit_or_answer(callback, f"📡 <b>Ноды ({len(nodes)})</b>", reply_markup=nodes_kb(nodes))


@router.callback_query(F.data.startswith("node:"))
async def view_node(callback: CallbackQuery, session: AsyncSession):
    node_uuid = callback.data.split(":", 1)[1]
    nodes = await remnawave.get_nodes()
    node = next((n for n in nodes if str(n.uuid) == node_uuid), None)
    if not node:
        await callback.answer("Нода не найдена", show_alert=True)
        return
    status = "🟢 Онлайн" if node.is_connected else "🔴 Офлайн"
    uptime = await dal.get_node_uptime(session, node_uuid)
    latency = await dal.get_node_avg_latency(session, node_uuid)
    check_info = f"\n📈 Аптайм 24ч: {uptime}%" if uptime is not None else "\n📈 Аптайм 24ч: нет данных"
    if latency is not None:
        check_info += f" | ⚡ {latency} мс"
    await edit_or_answer(
        callback,
        f"📡 <b>{node.name}</b>\n\nСтатус: {status}\nАдрес: {node.address}\nUUID: <code>{node_uuid}</code>{check_info}",
        reply_markup=node_manage_kb(node_uuid),
    )


@router.callback_query(F.data.startswith("restart_node:"))
async def restart_node(callback: CallbackQuery):
    ok = await remnawave.restart_node(callback.data.split(":", 1)[1])
    await callback.answer("🔄 Нода перезагружается..." if ok else "❌ Ошибка перезагрузки", show_alert=True)


@router.callback_query(F.data == "admin_torrent_blocks")
async def admin_torrent_blocks(callback: CallbackQuery, session: AsyncSession):
    blocks = await dal.get_recent_torrent_blocks(session)
    if not blocks:
        await edit_or_answer(callback, "✅ Блокировок не было.", reply_markup=admin_nav_kb("admin_cat_infra"))
        return
    lines = ["🧲 <b>Последние торрент-блоки:</b>\n"]
    for b in blocks:
        date_str = b.created_at.strftime("%d.%m %H:%M")
        lines.append(
            f"{date_str} — <code>{b.username or '—'}</code> | {b.ip or '—'} | {b.node_name or '—'}"
        )
    await edit_or_answer(callback, "\n".join(lines), reply_markup=admin_nav_kb("admin_cat_infra"))
