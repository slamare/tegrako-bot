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

from bot.filters import AdminFilter
from bot.states.states import AdminSG
from bot.keyboards.admin_kb import (
    admin_menu_kb, admin_sales_kb, admin_users_section_kb, admin_infra_kb,
    admin_comms_kb, admin_system_kb, payment_approve_kb, ticket_reply_kb,
    tariff_list_kb, tariff_manage_kb, nodes_kb, node_manage_kb,
    user_manage_kb, broadcast_target_kb, promo_list_kb, access_mode_kb,
)
from bot.handlers.user.start import LIFETIME_DAYS_THRESHOLD
from bot.keyboards.user_kb import main_menu_kb
from bot.services import remnawave
from bot.utils.helpers import edit_or_answer, cleanup_fsm_interaction, delete_later
from config.settings import settings
from db import dal
from db.models import Payment
from bot.handlers.admin._shared import admin_nav_kb, _provision_mtproto

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


async def _mark_payment(msg: Message, approved: bool) -> None:
    suffix = "\n\n✅ <b>ПОДТВЕРЖДЕНО</b>" if approved else "\n\n❌ <b>ОТКЛОНЕНО</b>"
    try:
        if msg.photo:
            await msg.edit_caption(caption=(msg.caption or "") + suffix, parse_mode="HTML")
        else:
            await msg.edit_text((msg.text or "") + suffix, parse_mode="HTML")
    except Exception:
        pass


# ── /admin ────────────────────────────────────────────────────────────────────

async def _admin_counts(session: AsyncSession) -> tuple[int, int]:
    pending = await dal.get_pending_payments(session)
    tickets = await dal.get_open_tickets(session)
    return len(pending), len(tickets)


@router.message(Command("admin"))
@router.message(F.text == "️ Администратор")
async def admin_panel(message: Message, session: AsyncSession):
    pending_count, tickets_count = await _admin_counts(session)
    await message.answer(
        "⚙️ <b>Панель администратора</b>", parse_mode="HTML",
        reply_markup=admin_menu_kb(pending_count=pending_count, tickets_count=tickets_count),
    )


@router.callback_query(F.data == "admin_menu")
async def admin_menu_cb(callback: CallbackQuery, session: AsyncSession):
    pending_count, tickets_count = await _admin_counts(session)
    await edit_or_answer(
        callback,
        "⚙️ <b>Панель администратора</b>",
        reply_markup=admin_menu_kb(pending_count=pending_count, tickets_count=tickets_count),
    )


@router.callback_query(F.data == "admin_cat_sales")
async def admin_cat_sales(callback: CallbackQuery, session: AsyncSession):
    pending_count, _ = await _admin_counts(session)
    await edit_or_answer(callback, "💰 <b>Продажи</b>", reply_markup=admin_sales_kb(pending_count=pending_count))


@router.callback_query(F.data == "admin_cat_users")
async def admin_cat_users(callback: CallbackQuery, session: AsyncSession):
    _, tickets_count = await _admin_counts(session)
    await edit_or_answer(callback, "👥 <b>Пользователи</b>", reply_markup=admin_users_section_kb(tickets_count=tickets_count))


@router.callback_query(F.data == "admin_cat_infra")
async def admin_cat_infra(callback: CallbackQuery):
    await edit_or_answer(callback, "🖥 <b>Инфраструктура</b>", reply_markup=admin_infra_kb())


@router.callback_query(F.data == "admin_cat_comms")
async def admin_cat_comms(callback: CallbackQuery):
    await edit_or_answer(callback, "📣 <b>Коммуникации</b>", reply_markup=admin_comms_kb())


@router.callback_query(F.data == "admin_cat_system")
async def admin_cat_system(callback: CallbackQuery, session: AsyncSession):
    maintenance = await dal.get_setting(session, "maintenance", "0")
    await edit_or_answer(callback, "⚙️ <b>Система</b>", reply_markup=admin_system_kb(maintenance_on=maintenance == "1"))


# ── Статистика ────────────────────────────────────────────────────────────────


# ── Платежи ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_pending_payments")
async def admin_pending_payments(callback: CallbackQuery, session: AsyncSession):
    payments = await dal.get_pending_payments(session)
    if not payments:
        await edit_or_answer(callback, "✅ Нет ожидающих оплат.", reply_markup=admin_nav_kb("admin_cat_sales"))
        return
    await edit_or_answer(
        callback,
        f"⏳ Ожидающих: {len(payments)}. Карточки ниже 👇",
        reply_markup=admin_nav_kb("admin_cat_sales"),
    )
    for p in payments:
        u, t = p.user, p.tariff
        text = (
            f"💳 <b>Оплата #{p.id}</b>\n"
            f"👤 @{u.username or '—'} (<code>{u.telegram_id}</code>)\n"
            f"🆔 <code>{u.remnawave_username or '—'}</code>\n"
            f"📦 {t.name if t else '?'} | 💰 {int(p.amount)} ₽ | {p.payment_method or '—'}"
        )
        if p.screenshot_file_id:
            await callback.message.answer_photo(
                p.screenshot_file_id, caption=text,
                parse_mode="HTML", reply_markup=payment_approve_kb(p.id),
            )
        else:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=payment_approve_kb(p.id))


@router.callback_query(F.data.startswith("approve:"))
async def approve_payment(callback: CallbackQuery, session: AsyncSession):
    payment_id = int(callback.data.split(":")[1])
    result = await session.execute(
        select(Payment).options(selectinload(Payment.user), selectinload(Payment.tariff))
        .where(Payment.id == payment_id)
    )
    payment = result.scalar_one_or_none()
    if not payment or payment.status != "pending":
        await callback.answer("Платёж уже обработан", show_alert=True)
        return

    user, tariff = payment.user, payment.tariff
    try:
        if payment.payment_type == "device_slot":
            new_slots = user.extra_device_slots + 1
            await dal.update_user(session, user.telegram_id, extra_device_slots=new_slots)
            if user.remnawave_uuid:
                rw = await remnawave.get_user_by_uuid(user.remnawave_uuid)
                if rw:
                    await remnawave.update_user_limits(
                        user.remnawave_uuid, device_limit=rw.hwid_device_limit + 1
                    )
            await dal.update_payment(session, payment_id, status="approved", approved_by=callback.from_user.id)
            await callback.bot.send_message(
                user.telegram_id,
                "✅ <b>Лимит устройств увеличен!</b>\n\nТеперь вы можете подключить ещё одно устройство.",
                disable_notification=True, parse_mode="HTML",
            )
        else:
            if not user.remnawave_uuid and not user.remnawave_username:
                await callback.answer(
                    "❌ У пользователя не задан аккаунт. Попросите пройти /start.", show_alert=True,
                )
                return
            squad_uuid = tariff.squad_uuid if tariff else None
            if user.remnawave_uuid:
                await remnawave.extend_subscription(user.remnawave_uuid, tariff.duration_days)
                remnawave.invalidate_sub_info_cache(user.remnawave_uuid)
                await remnawave.add_user_to_default_squad(user.remnawave_uuid, squad_uuid)
            else:
                rw_user = await remnawave.create_user(
                    username=user.remnawave_username,
                    duration_days=tariff.duration_days,
                    traffic_limit_gb=tariff.traffic_limit_gb,
                    device_limit=tariff.device_limit,
                    telegram_id=user.telegram_id,
                )
                await dal.update_user(session, user.telegram_id, remnawave_uuid=str(rw_user.uuid))
                await remnawave.add_user_to_default_squad(str(rw_user.uuid), squad_uuid)

            await dal.update_payment(session, payment_id, status="approved", approved_by=callback.from_user.id)
            await _provision_mtproto(session, user, tariff)

            if payment.promo_id:
                await dal.use_promo(session, payment.promo_id)

            ref_rate = int(await dal.get_setting(session, "referral_days", "0"))
            ref_days = round(tariff.duration_days / 30 * ref_rate) if tariff else 0
            if ref_days > 0 and user.referred_by:
                referrer = await dal.get_user(session, user.referred_by)
                if referrer and referrer.remnawave_uuid:
                    try:
                        referrer_rw = await remnawave.get_subscription_info(referrer.remnawave_uuid)
                        referrer_is_lifetime = bool(
                            referrer_rw and referrer_rw.status.value == "ACTIVE"
                            and (referrer_rw.expire_at - datetime.now(timezone.utc)).days > LIFETIME_DAYS_THRESHOLD
                        )
                        if not referrer_is_lifetime:
                            await remnawave.extend_subscription(referrer.remnawave_uuid, ref_days)
                            remnawave.invalidate_sub_info_cache(referrer.remnawave_uuid)
                            await callback.bot.send_message(
                                referrer.telegram_id,
                                f"🎁 <b>Реферальный бонус!</b>\n\n"
                                f"Ваш друг @{user.username or user.telegram_id} оплатил подписку.\n"
                                f"Вам начислено <b>+{ref_days} дней</b>.",
                                parse_mode="HTML", disable_notification=True,
                            )
                    except Exception:
                        pass

            # Новому пользователю (ни разу не подключался) — инструкция по клиентам
            ever_connected = await dal.was_notified(session, user.id, "wh_first_connected")
            if not ever_connected:
                confirm_text = (
                    f"✅ <b>Оплата подтверждена!</b>\n\n"
                    f"Тариф: {tariff.name} ({tariff.duration_days} дн.)\n\n"
                    f"📱 <b>Как подключиться:</b>\n"
                    f"Перейдите в «👤 Личный кабинет» → «Моя подписка» → "
                    f"нажмите «🔗 Открыть подписку».\n\n"
                    f"Вставьте ссылку в VPN-клиент:\n"
                    f"• iOS — <a href=\"https://apps.apple.com/app/streisand/id6450534064\">Streisand</a>\n"
                    f"• Android — <a href=\"https://play.google.com/store/apps/details?id=com.v2rayng.v2rayNG\">v2rayNG</a>\n"
                    f"• Windows — <a href=\"https://github.com/2dust/v2rayN/releases/latest\">v2rayN</a>"
                )
            else:
                confirm_text = (
                    f"✅ <b>Оплата подтверждена!</b>\n\nТариф: {tariff.name} ({tariff.duration_days} дн.)\n"
                    f"Перейдите в Личный кабинет → Моя подписка."
                )
            await callback.bot.send_message(
                user.telegram_id,
                confirm_text,
                parse_mode="HTML", disable_notification=True,
                disable_web_page_preview=True,
            )

        await _mark_payment(callback.message, approved=True)
        await callback.answer("✅ Подтверждено")
    except Exception as e:
        await callback.answer(f"Ошибка: {str(e)[:100]}", show_alert=True)


@router.callback_query(F.data.startswith("reject:"))
async def reject_payment(callback: CallbackQuery, session: AsyncSession):
    payment_id = int(callback.data.split(":")[1])
    result = await session.execute(
        select(Payment).options(selectinload(Payment.user), selectinload(Payment.tariff))
        .where(Payment.id == payment_id)
    )
    payment = result.scalar_one_or_none()
    if not payment or payment.status != "pending":
        await callback.answer("Платёж уже обработан", show_alert=True)
        return
    await dal.update_payment(session, payment_id, status="rejected")
    await callback.bot.send_message(
        payment.user.telegram_id,
        "❌ <b>Оплата отклонена.</b>\nЕсли считаете ошибкой — обратитесь в поддержку.",
        disable_notification=True, parse_mode="HTML",
    )
    await _mark_payment(callback.message, approved=False)
    await callback.answer("❌ Отклонено")


from bot.handlers.admin import (
    stats, tickets, tariffs, promos, hosts, nodes,
    maintenance, users, custom_buttons, broadcast, misc,
)

for _module in (stats, tickets, tariffs, promos, hosts, nodes,
                maintenance, users, custom_buttons, broadcast, misc):
    router.include_router(_module.router)
