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

# ── Тарифы ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_tariffs")
async def admin_tariffs(callback: CallbackQuery, session: AsyncSession):
    tariffs = await dal.get_all_tariffs(session)
    await edit_or_answer(callback, "📦 <b>Тарифы</b>", reply_markup=tariff_list_kb(tariffs))


@router.callback_query(F.data.startswith("admin_tariff:"))
async def view_tariff(callback: CallbackQuery, session: AsyncSession):
    t = await dal.get_tariff(session, int(callback.data.split(":")[1]))
    if not t:
        await callback.answer("Не найден", show_alert=True)
        return
    traffic = f"{t.traffic_limit_gb} ГБ" if t.traffic_limit_gb else "Безлимит"
    squad_info = f"\n🔗 Сквад: <code>{t.squad_uuid}</code>" if t.squad_uuid else "\n🔗 Сквад: дефолтный"
    if t.is_trial:
        type_info = "\n🎁 Тип: <b>Триальный</b>"
    elif t.is_referral:
        type_info = "\n👥 Тип: <b>Реферальный</b>"
    else:
        type_info = "\n📦 Тип: Обычный"
    await edit_or_answer(
        callback,
        f"📦 <b>{t.name}</b>\n{t.description or ''}\n"
        f"⏱ {t.duration_days} дн. | 📊 {traffic} | "
        f"📱 {t.device_limit or '∞'} уст. | 💰 {int(t.price)} ₽\n"
        f"{'✅ Активен' if t.is_active else '❌ Неактивен'}"
        f"{squad_info}{type_info}",
        reply_markup=tariff_manage_kb(t.id, t.is_active, t.is_trial, t.is_referral),
    )


@router.callback_query(F.data == "admin_create_tariff")
async def create_tariff_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSG.tariff_name)
    msg = await callback.message.answer("📦 Создание тарифа\n\nВведите <b>название</b>:", parse_mode="HTML")
    await state.update_data(bot_prompt_msg_id=msg.message_id)
    await callback.answer()


@router.message(AdminSG.tariff_name, F.text)
async def tariff_name(message: Message, state: FSMContext):
    await cleanup_fsm_interaction(message, state)
    await state.update_data(name=message.text.strip())
    await state.set_state(AdminSG.tariff_description)
    msg = await message.answer("Введите <b>описание</b> (или '-' пропустить):", parse_mode="HTML")
    await state.update_data(bot_prompt_msg_id=msg.message_id)


@router.message(AdminSG.tariff_description, F.text)
async def tariff_description(message: Message, state: FSMContext):
    await cleanup_fsm_interaction(message, state)
    await state.update_data(description=None if message.text.strip() == "-" else message.text.strip())
    await state.set_state(AdminSG.tariff_days)
    msg = await message.answer("Введите <b>количество дней</b>:", parse_mode="HTML")
    await state.update_data(bot_prompt_msg_id=msg.message_id)


async def _int_step(message, state, field, next_state, prompt, min_val=0):
    try:
        val = int(message.text.strip())
        assert val >= min_val
    except Exception:
        await cleanup_fsm_interaction(message, state)
        msg = await message.answer(f"❌ Введите целое число {'> 0' if min_val > 0 else '>= 0'}:")
        await state.update_data(bot_prompt_msg_id=msg.message_id)
        return
    await cleanup_fsm_interaction(message, state)
    await state.update_data(**{field: val})
    await state.set_state(next_state)
    msg = await message.answer(prompt, parse_mode="HTML")
    await state.update_data(bot_prompt_msg_id=msg.message_id)


@router.message(AdminSG.tariff_days, F.text)
async def tariff_days(message: Message, state: FSMContext):
    await _int_step(message, state, "duration_days", AdminSG.tariff_traffic,
                    "Введите <b>лимит трафика ГБ</b> (0 = безлимит):", min_val=1)


@router.message(AdminSG.tariff_traffic, F.text)
async def tariff_traffic(message: Message, state: FSMContext):
    await _int_step(message, state, "traffic_limit_gb", AdminSG.tariff_devices,
                    "Введите <b>лимит устройств</b> (0 = безлимит):")


@router.message(AdminSG.tariff_devices, F.text)
async def tariff_devices(message: Message, state: FSMContext):
    await _int_step(message, state, "device_limit", AdminSG.tariff_price,
                    "Введите <b>цену</b> в рублях:")


@router.message(AdminSG.tariff_price, F.text)
async def tariff_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.strip().replace(",", "."))
        assert price > 0
    except Exception:
        await cleanup_fsm_interaction(message, state)
        msg = await message.answer("❌ Введите число > 0:")
        await state.update_data(bot_prompt_msg_id=msg.message_id)
        return
    await cleanup_fsm_interaction(message, state)
    await state.update_data(price=price)
    await state.set_state(AdminSG.tariff_squad)
    msg = await message.answer("Введите <b>UUID сквада</b> (или '-' для дефолтного):", parse_mode="HTML")
    await state.update_data(bot_prompt_msg_id=msg.message_id)


@router.message(AdminSG.tariff_squad, F.text)
async def tariff_squad(message: Message, state: FSMContext):
    await cleanup_fsm_interaction(message, state)
    text = message.text.strip()
    await state.update_data(squad_uuid=None if text == "-" else text)
    await state.set_state(AdminSG.tariff_trial)
    msg = await message.answer(
        "Это <b>триальный</b> тариф? (только новорегам без подписки)\n\nОтправьте <b>да</b> или <b>нет</b>.",
        parse_mode="HTML",
    )
    await state.update_data(bot_prompt_msg_id=msg.message_id)


@router.message(AdminSG.tariff_trial, F.text)
async def tariff_trial(message: Message, state: FSMContext):
    await cleanup_fsm_interaction(message, state)
    await state.update_data(is_trial=message.text.strip().lower() in ("да", "yes", "1", "true", "+"))
    await state.set_state(AdminSG.tariff_referral)
    msg = await message.answer(
        "Это <b>реферальный</b> тариф?\n\nОтправьте <b>да</b> или <b>нет</b>.",
        parse_mode="HTML",
    )
    await state.update_data(bot_prompt_msg_id=msg.message_id)


@router.message(AdminSG.tariff_referral, F.text)
async def tariff_referral(message: Message, session: AsyncSession, state: FSMContext):
    await cleanup_fsm_interaction(message, state)
    data = await state.get_data()
    data["is_referral"] = message.text.strip().lower() in ("да", "yes", "1", "true", "+")
    t = await dal.create_tariff(session, **data)
    await state.clear()
    squad_info = f"сквад: {data.get('squad_uuid')}" if data.get("squad_uuid") else "дефолтный сквад"
    badge = " | 🎁 Триальный" if data.get("is_trial") else (" | 👥 Реферальный" if data.get("is_referral") else "")
    msg = await message.answer(
        f"✅ Тариф <b>{t.name}</b> создан! {t.duration_days} дн. | {int(t.price)} ₽ | {squad_info}{badge}",
        parse_mode="HTML",
    )
    asyncio.create_task(delete_later(message.bot, message.chat.id, msg.message_id, 30))


async def _reload_tariff_kb(callback, session, tariff_id):
    t = await dal.get_tariff(session, tariff_id)
    await callback.message.edit_reply_markup(
        reply_markup=tariff_manage_kb(tariff_id, t.is_active, t.is_trial, t.is_referral)
    )


@router.callback_query(F.data.startswith("toggle_tariff:"))
async def toggle_tariff(callback: CallbackQuery, session: AsyncSession):
    tariff_id = int(callback.data.split(":")[1])
    t = await dal.get_tariff(session, tariff_id)
    if not t:
        await callback.answer("Не найден", show_alert=True)
        return
    await dal.update_tariff(session, tariff_id, is_active=not t.is_active)
    await callback.answer("Статус обновлён")
    await _reload_tariff_kb(callback, session, tariff_id)


@router.callback_query(F.data.startswith("toggle_trial:"))
async def toggle_trial(callback: CallbackQuery, session: AsyncSession):
    tariff_id = int(callback.data.split(":")[1])
    t = await dal.get_tariff(session, tariff_id)
    if not t:
        await callback.answer("Не найден", show_alert=True)
        return
    await dal.update_tariff(session, tariff_id, is_trial=not t.is_trial)
    await callback.answer("🎁 Триальный включён" if not t.is_trial else "🔓 Триал снят", show_alert=True)
    await _reload_tariff_kb(callback, session, tariff_id)


@router.callback_query(F.data.startswith("toggle_referral:"))
async def toggle_referral(callback: CallbackQuery, session: AsyncSession):
    tariff_id = int(callback.data.split(":")[1])
    t = await dal.get_tariff(session, tariff_id)
    if not t:
        await callback.answer("Не найден", show_alert=True)
        return
    await dal.update_tariff(session, tariff_id, is_referral=not t.is_referral)
    await callback.answer("👥 Реферальный включён" if not t.is_referral else "🔓 Реферальный снят", show_alert=True)
    await _reload_tariff_kb(callback, session, tariff_id)


@router.callback_query(F.data.startswith("delete_tariff:"))
async def delete_tariff(callback: CallbackQuery, session: AsyncSession):
    await dal.delete_tariff(session, int(callback.data.split(":")[1]))
    await callback.answer("Удалён")
    await edit_or_answer(callback, "📦 <b>Тарифы</b>", reply_markup=tariff_list_kb(await dal.get_all_tariffs(session)))
