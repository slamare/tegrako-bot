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

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


def admin_nav_kb(back_callback: str = "admin_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=back_callback)],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])


async def _provision_mtproto(session: AsyncSession, user, tariff) -> None:
    """Выдаёт или обновляет MTProto-секрет пользователя в telemt."""
    try:
        from bot.services import telemt as telemt_svc
        from bot.services import remnawave
        
        # Получаем hwid_device_limit из Remnawave API
        rw = await remnawave.get_subscription_info(user.remnawave_uuid) if user.remnawave_uuid else None
        hwid_limit = rw.hwid_device_limit if rw else 0
        
        # Если лимит не установлен (0) — ставим 5 для поддержки сторонних клиентов
        max_ips = max(1, hwid_limit) if hwid_limit else 5
        if not user.mtproto_secret:
            secret = telemt_svc.generate_secret()
            await telemt_svc.add_user(user.remnawave_username, secret, max_ips=max_ips)
            await dal.update_user(session, user.telegram_id, mtproto_secret=secret)
        else:
            await telemt_svc.add_user(user.remnawave_username, user.mtproto_secret, max_ips=max_ips)
    except Exception as e:
        logger.warning(f"MTProto provision failed for {user.telegram_id}: {e}")


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

@router.message(Command("admin"))
@router.message(F.text == "️ Администратор")
async def admin_panel(message: Message, session: AsyncSession):
    maintenance = await dal.get_setting(session, "maintenance", "0")
    await message.answer(
        "⚙️ <b>Панель администратора</b>", parse_mode="HTML",
        reply_markup=admin_menu_kb(maintenance_on=maintenance == "1"),
    )


@router.callback_query(F.data == "admin_menu")
async def admin_menu_cb(callback: CallbackQuery, session: AsyncSession):
    maintenance = await dal.get_setting(session, "maintenance", "0")
    await edit_or_answer(
        callback,
        "⚙️ <b>Панель администратора</b>",
        reply_markup=admin_menu_kb(maintenance_on=maintenance == "1"),
    )


# ── Статистика ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery, session: AsyncSession):
    users = await dal.count_users(session)
    revenue = await dal.get_revenue_stats(session)
    pending = await dal.get_pending_payments(session)
    ref_days = await dal.get_setting(session, "referral_days", "0")
    try:
        nodes = await remnawave.get_nodes()
        nodes_online = sum(1 for n in nodes if n.is_connected)
        panel_text = f"\n\n<b>Ноды:</b> {nodes_online}/{len(nodes)} онлайн"
    except Exception:
        panel_text = "\n\n⚠️ Не удалось получить данные панели"

    reset_at = await dal.get_setting(session, "revenue_reset_at", "")
    reset_note = f"\n<i>Выручка с {reset_at[:10]}</i>" if reset_at else ""
    text = (
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Всего: {users['total']} | Зарег.: {users['registered']} | Бан: {users['banned']}\n"
        f"⏳ Ожидают оплаты: {len(pending)}\n\n"
        f"<b>Выручка:</b>\n"
        f"📅 Неделя: {revenue['weekly']:.0f} ₽\n"
        f"📆 Месяц: {revenue['monthly']:.0f} ₽\n"
        f"💰 Всего: {revenue['total']:.0f} ₽{reset_note}\n\n"
        f"🎁 Бонус за реферала: <b>{ref_days} дн.</b>"
        f"{panel_text}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="️ Изменить бонус за реферала", callback_data="admin_set_ref_days")],
        [InlineKeyboardButton(text="🗑 Сбросить выручку", callback_data="admin_reset_revenue")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])
    await edit_or_answer(callback, text, reply_markup=kb)


@router.callback_query(F.data == "admin_reset_revenue")
async def admin_reset_revenue(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, сбросить", callback_data="admin_reset_revenue_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])
    await edit_or_answer(
        callback,
        "⚠️ <b>Сбросить статистику выручки?</b>\n\nСтатистика начнёт считаться заново с сегодняшнего дня.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "admin_reset_revenue_confirm")
async def admin_reset_revenue_confirm(callback: CallbackQuery, session: AsyncSession):
    await dal.set_setting(session, "revenue_reset_at", datetime.utcnow().isoformat())
    await callback.answer("✅ Выручка сброшена", show_alert=True)
    await admin_stats(callback, session)


@router.callback_query(F.data == "admin_set_ref_days")
async def admin_set_ref_days(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSG.set_referral_days)
    msg = await callback.message.answer(
        "Введите количество дней за каждого оплатившего реферала.\n\n"
        "Введите <b>0</b> чтобы отключить бонус.",
        parse_mode="HTML",
    )
    await state.update_data(bot_prompt_msg_id=msg.message_id)
    await callback.answer()


@router.message(AdminSG.set_referral_days, F.text)
async def save_referral_days(message: Message, session: AsyncSession, state: FSMContext):
    try:
        days = int(message.text.strip())
        assert days >= 0
    except Exception:
        await cleanup_fsm_interaction(message, state)
        msg = await message.answer("❌ Введите целое число >= 0:")
        await state.update_data(bot_prompt_msg_id=msg.message_id)
        return
    await dal.set_setting(session, "referral_days", str(days))
    await cleanup_fsm_interaction(message, state)
    await state.clear()
    msg = await message.answer(f"✅ Бонус за реферала: <b>{days} дн.</b>", parse_mode="HTML")
    asyncio.create_task(delete_later(message.bot, message.chat.id, msg.message_id, 30))


# ── Режимы доступа ────────────────────────────────────────────────────────────

ACCESS_MODE_LABELS = {
    "open": "🟢 Открытый доступ",
    "closed": "🔴 Полное ограничение",
    "invite_only": "📨 Только по приглашениям",
    "no_purchase": "🚫 Запрет покупок",
    "no_register": "🚫 Запрет регистрации",
}
ACCESS_MODE_DESC = {
    "open": "Бот работает в обычном режиме.",
    "closed": "Все пользователи получают сообщение о недоступности.",
    "invite_only": "Регистрация только по реферальной ссылке.",
    "no_purchase": "Покупки заблокированы, бот доступен.",
    "no_register": "Регистрация закрыта, существующие пользователи работают.",
}


@router.callback_query(F.data == "admin_access_mode")
async def admin_access_mode(callback: CallbackQuery, session: AsyncSession):
    current = await dal.get_setting(session, "access_mode", "open")
    label = ACCESS_MODE_LABELS.get(current, current)
    desc = ACCESS_MODE_DESC.get(current, "")
    await edit_or_answer(
        callback,
        f"🔐 <b>Режим доступа</b>\n\nТекущий: <b>{label}</b>\n<i>{desc}</i>\n\nВыберите новый режим:",
        reply_markup=access_mode_kb(current),
    )


@router.callback_query(F.data.startswith("set_access_mode:"))
async def set_access_mode(callback: CallbackQuery, session: AsyncSession):
    mode = callback.data.split(":", 1)[1]
    if mode not in ACCESS_MODE_LABELS:
        await callback.answer("Неизвестный режим", show_alert=True)
        return
    await dal.set_setting(session, "access_mode", mode)
    await callback.answer(f"✅ {ACCESS_MODE_LABELS[mode]}", show_alert=True)
    await admin_access_mode(callback, session)


# ── Платежи ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_pending_payments")
async def admin_pending_payments(callback: CallbackQuery, session: AsyncSession):
    payments = await dal.get_pending_payments(session)
    if not payments:
        await edit_or_answer(callback, "✅ Нет ожидающих оплат.", reply_markup=admin_nav_kb())
        return
    await edit_or_answer(
        callback,
        f"⏳ Ожидающих: {len(payments)}. Карточки ниже 👇",
        reply_markup=admin_nav_kb(),
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

            ref_days = int(await dal.get_setting(session, "referral_days", "0"))
            if ref_days > 0 and user.referred_by:
                referrer = await dal.get_user(session, user.referred_by)
                if referrer and referrer.remnawave_uuid:
                    try:
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
    builder.button(text="◀️ Назад", callback_data="admin_menu")
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
    await _mark_payment(callback.message, approved=True)
    await callback.answer("Закрыт")


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
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")],
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
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])
    await edit_or_answer(callback, f"🌐 <b>Хосты ({len(hosts)})</b>\n\n" + "\n\n".join(lines), reply_markup=kb)


# ── Ноды ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_nodes")
async def admin_nodes(callback: CallbackQuery):
    nodes = await remnawave.get_nodes()
    if not nodes:
        await callback.answer("Ноды не найдены", show_alert=True)
        return
    await edit_or_answer(callback, f"📡 <b>Ноды ({len(nodes)})</b>", reply_markup=nodes_kb(nodes))


@router.callback_query(F.data.startswith("node:"))
async def view_node(callback: CallbackQuery):
    node_uuid = callback.data.split(":", 1)[1]
    nodes = await remnawave.get_nodes()
    node = next((n for n in nodes if str(n.uuid) == node_uuid), None)
    if not node:
        await callback.answer("Нода не найдена", show_alert=True)
        return
    status = "🟢 Онлайн" if node.is_connected else "🔴 Офлайн"
    await edit_or_answer(
        callback,
        f"📡 <b>{node.name}</b>\n\nСтатус: {status}\nАдрес: {node.address}\nUUID: <code>{node_uuid}</code>",
        reply_markup=node_manage_kb(node_uuid),
    )


@router.callback_query(F.data.startswith("restart_node:"))
async def restart_node(callback: CallbackQuery):
    ok = await remnawave.restart_node(callback.data.split(":", 1)[1])
    await callback.answer("🔄 Нода перезагружается..." if ok else "❌ Ошибка перезагрузки", show_alert=True)


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


# ── Пользователи ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery, session: AsyncSession):
    users = await dal.get_all_users(session, only_registered=True)
    builder = InlineKeyboardBuilder()
    for u in users[:20]:
        builder.button(
            text=f"@{u.username or '—'} | {u.remnawave_username or '?'}",
            callback_data=f"admin_user:{u.telegram_id}",
        )
    builder.button(text="🔍 Поиск", callback_data="admin_search_user")
    builder.button(text="🚫 Забаненные", callback_data="admin_banned_users")
    builder.button(text="◀️ Назад", callback_data="admin_menu")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)
    await edit_or_answer(callback, f"👥 <b>Пользователи ({len(users)})</b>", reply_markup=builder.as_markup())


@router.callback_query(F.data == "admin_search_user")
async def admin_search_user_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminSG.search_user)
    msg = await callback.message.answer("Введите username, имя аккаунта или Telegram ID:")
    await state.update_data(bot_prompt_msg_id=msg.message_id)
    await callback.answer()


@router.message(AdminSG.search_user, F.text)
async def admin_search_user(message: Message, session: AsyncSession, state: FSMContext):
    await cleanup_fsm_interaction(message, state)
    users = await dal.search_users(session, message.text.strip())
    if not users:
        msg = await message.answer("Ничего не найдено.")
        asyncio.create_task(delete_later(message.bot, message.chat.id, msg.message_id, 30))
        return
    builder = InlineKeyboardBuilder()
    for u in users:
        builder.button(
            text=f"@{u.username or '—'} | {u.remnawave_username or '?'}",
            callback_data=f"admin_user:{u.telegram_id}",
        )
    builder.adjust(1)
    msg = await message.answer(f"🔍 Найдено: {len(users)}", reply_markup=builder.as_markup())
    await state.clear()
    asyncio.create_task(delete_later(message.bot, message.chat.id, msg.message_id, 30))


@router.callback_query(F.data == "admin_banned_users")
async def admin_banned_users(callback: CallbackQuery, session: AsyncSession):
    users = await dal.get_banned_users(session)
    builder = InlineKeyboardBuilder()
    for u in users:
        builder.button(text=f"🚫 @{u.username or u.telegram_id}", callback_data=f"admin_user:{u.telegram_id}")
    builder.button(text="◀️ Назад", callback_data="admin_users")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)
    header = f"🚫 <b>Забаненные ({len(users)})</b>" if users else "✅ Забаненных нет."
    await edit_or_answer(callback, header, reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("admin_user:"))
async def view_user(callback: CallbackQuery, session: AsyncSession):
    tg_id = int(callback.data.split(":")[1])
    user = await dal.get_user(session, tg_id)
    if not user:
        await callback.answer("Не найден", show_alert=True)
        return
    sub_info = "—"
    if user.remnawave_uuid:
        try:
            rw = await remnawave.get_subscription_info(user.remnawave_uuid)
            if rw:
                sub_info = f"{rw.status.value} до {rw.expire_at.strftime('%d.%m.%Y')}"
        except Exception:
            pass
    ref_count = await dal.count_referrals(session, tg_id)
    ref_paid = await dal.get_referrals_with_payment(session, tg_id)
    referrer_info = f"\nПривёл: <code>{user.referred_by}</code>" if user.referred_by else ""
    slots_info = f"\n📱 Доп. слоты: {user.extra_device_slots}" if user.extra_device_slots else ""
    role_icon = {"developer": "‍💻", "admin": "⚙️", "user": "👤"}.get(user.role, "👤")
    await edit_or_answer(
        callback,
        f"{role_icon} TG: <code>{tg_id}</code> | @{user.username or '—'}\n"
        f"Аккаунт: <code>{user.remnawave_username or '—'}</code>\n"
        f"Подписка: {sub_info}\n"
        f"Забанен: {'🚫 Да' if user.is_banned else '✅ Нет'}"
        f"{slots_info}\n"
        f"👥 Рефералов: {ref_count} (оплатили: {len(ref_paid)})"
        f"{referrer_info}\n"
        f"С: {user.created_at.strftime('%d.%m.%Y')}",
        reply_markup=user_manage_kb(tg_id, user.is_banned, user.remnawave_uuid),
    )


@router.callback_query(F.data.startswith("toggle_ban:"))
async def toggle_ban(callback: CallbackQuery, session: AsyncSession):
    tg_id = int(callback.data.split(":")[1])
    user = await dal.get_user(session, tg_id)
    if not user:
        await callback.answer("Не найден", show_alert=True)
        return
    new_ban = not user.is_banned
    await dal.update_user(session, tg_id, is_banned=new_ban)
    await callback.answer(f"{'🚫 Забанен' if new_ban else '✅ Разбанен'}", show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=user_manage_kb(tg_id, new_ban, user.remnawave_uuid))


@router.callback_query(F.data.startswith("admin_grant_unlimited:"))
async def admin_grant_unlimited(callback: CallbackQuery, session: AsyncSession):
    tg_id = int(callback.data.split(":")[1])
    user = await dal.get_user(session, tg_id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    ok = await remnawave.set_expire_at(user.remnawave_uuid, datetime(2099, 12, 31, 16, 59, 59, tzinfo=timezone.utc))
    if ok:
        remnawave.invalidate_sub_info_cache(user.remnawave_uuid)
        await callback.answer("✅ Бессрочный доступ до 31.12.2099", show_alert=True)
    else:
        await callback.answer("❌ Ошибка API", show_alert=True)


@router.callback_query(F.data.startswith("admin_assign_tariff:"))
async def admin_assign_tariff_start(callback: CallbackQuery, session: AsyncSession):
    tg_id = int(callback.data.split(":")[1])
    tariffs = await dal.get_active_tariffs(session)
    builder = InlineKeyboardBuilder()
    for t in tariffs:
        builder.button(text=f"{t.name} — {t.duration_days} дн.", callback_data=f"do_assign_tariff:{tg_id}:{t.id}")
    builder.button(text="◀️ Назад", callback_data=f"admin_user:{tg_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)
    await edit_or_answer(callback, "📦 Выберите тариф для назначения:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("do_assign_tariff:"))
async def do_assign_tariff(callback: CallbackQuery, session: AsyncSession):
    _, tg_id_str, tariff_id_str = callback.data.split(":")
    tg_id, tariff_id = int(tg_id_str), int(tariff_id_str)
    user = await dal.get_user(session, tg_id)
    tariff = await dal.get_tariff(session, tariff_id)
    if not user or not tariff:
        await callback.answer("Не найдено", show_alert=True)
        return
    try:
        squad_uuid = tariff.squad_uuid or settings.DEFAULT_SQUAD_UUID
        if user.remnawave_uuid:
            await remnawave.extend_subscription(user.remnawave_uuid, tariff.duration_days)
            await remnawave.update_user_limits(
                user.remnawave_uuid,
                traffic_limit_gb=tariff.traffic_limit_gb,
                device_limit=tariff.device_limit,
            )
            if squad_uuid:
                await remnawave.add_user_to_squad(user.remnawave_uuid, squad_uuid)
        else:
            rw_user = await remnawave.create_user(
                username=user.remnawave_username or f"user{tg_id}",
                duration_days=tariff.duration_days,
                traffic_limit_gb=tariff.traffic_limit_gb,
                device_limit=tariff.device_limit,
                telegram_id=tg_id,
            )
            await dal.update_user(session, tg_id, remnawave_uuid=str(rw_user.uuid))
            if squad_uuid:
                await remnawave.add_user_to_squad(str(rw_user.uuid), squad_uuid)
        remnawave.invalidate_sub_info_cache(user.remnawave_uuid)
        fresh_user = await dal.get_user(session, tg_id)
        if fresh_user:
            await _provision_mtproto(session, fresh_user, tariff)
        await callback.answer(f"✅ Тариф {tariff.name} назначен", show_alert=True)
        try:
            await callback.bot.send_message(
                tg_id,
                f"✅ <b>Администратор активировал подписку!</b>\n\nТариф: {tariff.name} ({tariff.duration_days} дн.)",
                parse_mode="HTML",
            )
        except Exception:
            pass
    except Exception as e:
        await callback.answer(f"Ошибка: {str(e)[:100]}", show_alert=True)


@router.callback_query(F.data.startswith("admin_sub_manage:"))
async def admin_sub_manage(callback: CallbackQuery, session: AsyncSession):
    tg_id = int(callback.data.split(":")[1])
    user = await dal.get_user(session, tg_id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    rw = await remnawave.get_user_by_uuid(user.remnawave_uuid)
    if not rw:
        await callback.answer("Не удалось получить данные", show_alert=True)
        return
    now = datetime.now(timezone.utc)
    days_left = (rw.expire_at - now).days
    used_gb = round(rw.user_traffic.used_traffic_bytes / 1024 ** 3, 2)
    limit_gb = round(rw.traffic_limit_bytes / 1024 ** 3, 1) if rw.traffic_limit_bytes else "∞"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Ссылка подписки", url=rw.subscription_url)],
        [InlineKeyboardButton(text="🔄 Сброс трафика", callback_data=f"admin_reset_traffic:{tg_id}")],
        [InlineKeyboardButton(
            text="✅ Включить" if rw.status.value != "ACTIVE" else "⛔ Отключить",
            callback_data=f"admin_toggle_sub:{tg_id}",
        )],
        [InlineKeyboardButton(text="🗑 Удалить из панели", callback_data=f"admin_delete_sub:{tg_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"admin_user:{tg_id}")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])
    await edit_or_answer(
        callback,
        f"📋 <b>Подписка пользователя</b>\n\n"
        f"Статус: {rw.status.value}\n"
        f"До: {rw.expire_at.strftime('%d.%m.%Y')} ({days_left} дн.)\n"
        f"Трафик: {used_gb} / {limit_gb} ГБ\n"
        f"Устройств: {rw.hwid_device_limit}",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("admin_reset_traffic:"))
async def admin_reset_traffic(callback: CallbackQuery, session: AsyncSession):
    tg_id = int(callback.data.split(":")[1])
    user = await dal.get_user(session, tg_id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    ok = await remnawave.reset_user_traffic(user.remnawave_uuid)
    await callback.answer("✅ Трафик сброшен" if ok else "❌ Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("admin_toggle_sub:"))
async def admin_toggle_sub(callback: CallbackQuery, session: AsyncSession):
    tg_id = int(callback.data.split(":")[1])
    user = await dal.get_user(session, tg_id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    rw = await remnawave.get_user_by_uuid(user.remnawave_uuid)
    if not rw:
        await callback.answer("Не удалось получить данные", show_alert=True)
        return
    new_status = "DISABLED" if rw.status.value == "ACTIVE" else "ACTIVE"
    await remnawave.set_user_status(user.remnawave_uuid, new_status)
    await callback.answer(
        "⛔ Подписка отключена" if new_status == "DISABLED" else "✅ Подписка включена",
        show_alert=True,
    )
    await admin_sub_manage(callback, session)


@router.callback_query(F.data.startswith("admin_delete_sub:"))
async def admin_delete_sub(callback: CallbackQuery, session: AsyncSession):
    tg_id = int(callback.data.split(":")[1])
    user = await dal.get_user(session, tg_id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"admin_delete_sub_confirm:{tg_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_sub_manage:{tg_id}")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ])
    await edit_or_answer(
        callback,
        "⚠️ <b>Удалить пользователя из панели?</b>\n\nПодписка и все данные будут удалены из Remnawave.",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("admin_delete_sub_confirm:"))
async def admin_delete_sub_confirm(callback: CallbackQuery, session: AsyncSession):
    tg_id = int(callback.data.split(":")[1])
    user = await dal.get_user(session, tg_id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    ok = await remnawave.delete_panel_user(user.remnawave_uuid)
    if ok:
        await dal.update_user(session, tg_id, remnawave_uuid=None)
        await callback.answer("✅ Удалено из панели", show_alert=True)
        await view_user(callback, session)
    else:
        await callback.answer("❌ Ошибка удаления", show_alert=True)


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
