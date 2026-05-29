from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.states.states import AdminSG
from bot.keyboards.admin_kb import (
    admin_menu_kb, payment_approve_kb, ticket_reply_kb,
    tariff_list_kb, tariff_manage_kb, nodes_kb, node_manage_kb,
    user_manage_kb, broadcast_target_kb,
)
from bot.keyboards.user_kb import main_menu_kb
from bot.services import remnawave
from config.settings import settings
from db import dal

router = Router()

def is_admin(tg_id: int) -> bool:
    return tg_id in settings.admin_ids

back_btn = lambda: [[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")]]


# ── /admin ────────────────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def admin_panel(message: Message, session: AsyncSession):
    if not is_admin(message.from_user.id): return
    maintenance = await dal.get_setting(session, "maintenance", "0")
    await message.answer("⚙️ <b>Панель администратора</b>", parse_mode="HTML",
                         reply_markup=admin_menu_kb(maintenance_on=maintenance == "1"))

@router.callback_query(F.data == "admin_menu")
async def admin_menu_cb(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id): return
    maintenance = await dal.get_setting(session, "maintenance", "0")
    await callback.message.edit_text("⚙️ <b>Панель администратора</b>", parse_mode="HTML",
                                     reply_markup=admin_menu_kb(maintenance_on=maintenance == "1"))

# ── Статистика ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id): return
    users = await dal.count_users(session)
    revenue = await dal.get_revenue_stats(session)
    pending = await dal.get_pending_payments(session)
    try:
        nodes = await remnawave.get_nodes()
        nodes_online = sum(1 for n in nodes if n.is_connected)
        panel_text = (
            f"\n\n<b>Ноды:</b> {nodes_online}/{len(nodes)} онлайн"
        )
    except Exception:
        panel_text = "\n\n⚠️ Не удалось получить данные панели"

    text = (
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Всего: {users['total']} | Зарег.: {users['registered']} | Бан: {users['banned']}\n"
        f"⏳ Ожидают оплаты: {len(pending)}\n\n"
        f"<b>Выручка:</b>\n"
        f"📅 Неделя: {revenue['weekly']:.0f} ₽\n"
        f"📆 Месяц: {revenue['monthly']:.0f} ₽\n"
        f"💰 Всего: {revenue['total']:.0f} ₽"
        f"{panel_text}"
    )
    await callback.message.edit_text(text, parse_mode="HTML",
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=back_btn()))

# ── Платежи ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_pending_payments")
async def admin_pending_payments(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id): return
    payments = await dal.get_pending_payments(session)
    if not payments:
        await callback.message.edit_text("✅ Нет ожидающих оплат.",
                                         reply_markup=InlineKeyboardMarkup(inline_keyboard=back_btn()))
        return
    await callback.message.edit_text(f"⏳ Ожидающих: {len(payments)}. Карточки ниже 👇",
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=back_btn()))
    for p in payments:
        u, t = p.user, p.tariff
        text = (f"💳 <b>Оплата #{p.id}</b>\n"
                f"👤 @{u.username or '—'} (<code>{u.telegram_id}</code>)\n"
                f"🆔 <code>{u.remnawave_username or '—'}</code>\n"
                f"📦 {t.name if t else '?'} | 💰 {int(p.amount)} ₽ | {p.payment_method or '—'}")
        if p.screenshot_file_id:
            await callback.message.answer_photo(p.screenshot_file_id, caption=text,
                                                parse_mode="HTML", reply_markup=payment_approve_kb(p.id))
        else:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=payment_approve_kb(p.id))

@router.callback_query(F.data.startswith("approve:"))
async def approve_payment(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id): return
    payment_id = int(callback.data.split(":")[1])
    payment = await dal.get_payment(session, payment_id)
    if not payment or payment.status != "pending":
        await callback.answer("Платёж уже обработан", show_alert=True); return
    user, tariff = payment.user, payment.tariff
    try:
        if user.remnawave_uuid:
            await remnawave.extend_subscription(user.remnawave_uuid, tariff.duration_days)
        else:
            rw_user = await remnawave.create_user(
                username=user.remnawave_username,
                duration_days=tariff.duration_days,
                traffic_limit_gb=tariff.traffic_limit_gb,
                device_limit=tariff.device_limit,
                telegram_id=user.telegram_id,
            )
            await dal.update_user(session, user.telegram_id, remnawave_uuid=str(rw_user.uuid))
        await dal.update_payment(session, payment_id, status="approved", approved_by=callback.from_user.id)
        await callback.bot.send_message(
            user.telegram_id,
            f"✅ <b>Оплата подтверждена!</b>\n\nТариф: {tariff.name} ({tariff.duration_days} дн.)\n"
            f"Перейдите в Личный кабинет → Моя подписка.",
            parse_mode="HTML", reply_markup=main_menu_kb(),
        )
        caption = (callback.message.caption or "") + "\n\n✅ <b>ПОДТВЕРЖДЕНО</b>"
        if callback.message.photo:
            await callback.message.edit_caption(caption=caption, parse_mode="HTML")
        else:
            await callback.message.edit_text(callback.message.text + "\n\n✅ <b>ПОДТВЕРЖДЕНО</b>", parse_mode="HTML")
        await callback.answer("✅ Подтверждено")
    except Exception as e:
        await callback.answer(f"Ошибка: {str(e)[:100]}", show_alert=True)

@router.callback_query(F.data.startswith("reject:"))
async def reject_payment(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id): return
    payment_id = int(callback.data.split(":")[1])
    payment = await dal.get_payment(session, payment_id)
    if not payment or payment.status != "pending":
        await callback.answer("Платёж уже обработан", show_alert=True); return
    await dal.update_payment(session, payment_id, status="rejected")
    await callback.bot.send_message(payment.user.telegram_id,
        "❌ <b>Оплата отклонена.</b>\nЕсли считаете ошибкой — обратитесь в поддержку.", parse_mode="HTML")
    caption = (callback.message.caption or "") + "\n\n❌ <b>ОТКЛОНЕНО</b>"
    if callback.message.photo:
        await callback.message.edit_caption(caption=caption, parse_mode="HTML")
    else:
        await callback.message.edit_text(callback.message.text + "\n\n❌ <b>ОТКЛОНЕНО</b>", parse_mode="HTML")
    await callback.answer("❌ Отклонено")

# ── Тикеты ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_tickets")
async def admin_tickets(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id): return
    tickets = await dal.get_open_tickets(session)
    if not tickets:
        await callback.message.edit_text("✅ Открытых тикетов нет.",
                                         reply_markup=InlineKeyboardMarkup(inline_keyboard=back_btn())); return
    builder = InlineKeyboardBuilder()
    for t in tickets:
        builder.button(text=f"#{t.id} — @{t.user.username or t.user.telegram_id}", callback_data=f"view_ticket:{t.id}")
    builder.button(text="◀️ Назад", callback_data="admin_menu")
    builder.adjust(1)
    await callback.message.edit_text(f"🎫 <b>Открытые тикеты: {len(tickets)}</b>",
                                     parse_mode="HTML", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("view_ticket:"))
async def view_ticket(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id): return
    ticket_id = int(callback.data.split(":")[1])
    ticket = await dal.get_ticket_by_id(session, ticket_id)
    if not ticket:
        await callback.answer("Тикет не найден", show_alert=True); return
    u = ticket.user
    msgs = ticket.messages[-5:]
    history = "".join(f"\n{'👤' if m.sender_role=='user' else '🛡'} {m.text or f'[{m.media_type}]'}" for m in msgs)
    await callback.message.edit_text(
        f"🎫 <b>Тикет #{ticket_id}</b>\n@{u.username or '—'} (<code>{u.telegram_id}</code>)\n"
        f"Аккаунт: <code>{u.remnawave_username or '—'}</code>\n\n<b>Последние сообщения:</b>{history or ' нет'}",
        parse_mode="HTML", reply_markup=ticket_reply_kb(ticket_id))

@router.callback_query(F.data.startswith("reply_ticket:"))
async def reply_ticket_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    ticket_id = int(callback.data.split(":")[1])
    await state.set_state(AdminSG.replying_ticket)
    await state.update_data(ticket_id=ticket_id)
    await callback.message.answer(f"✏️ Введите ответ на тикет #{ticket_id}:")
    await callback.answer()

@router.message(AdminSG.replying_ticket)
async def send_ticket_reply(message: Message, session: AsyncSession, state: FSMContext):
    if not is_admin(message.from_user.id): return
    data = await state.get_data()
    ticket = await dal.get_ticket_by_id(session, data.get("ticket_id"))
    if not ticket: await state.clear(); return
    await dal.add_ticket_message(session, ticket_id=ticket.id, sender_role="admin",
                                  sender_tg_id=message.from_user.id, text=message.text)
    try:
        await message.bot.send_message(ticket.user.telegram_id,
            f"💬 <b>Ответ поддержки (Тикет #{ticket.id}):</b>\n\n{message.text}", parse_mode="HTML")
        await message.answer("✅ Ответ отправлен.")
    except Exception:
        await message.answer("⚠️ Не удалось доставить сообщение.")
    await state.clear()

@router.callback_query(F.data.startswith("close_ticket:"))
async def close_ticket(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id): return
    ticket_id = int(callback.data.split(":")[1])
    ticket = await dal.get_ticket_by_id(session, ticket_id)
    if not ticket: await callback.answer("Не найден", show_alert=True); return
    await dal.close_ticket(session, ticket_id)
    try:
        await callback.bot.send_message(ticket.user.telegram_id,
            f"✅ Тикет #{ticket_id} закрыт. Если вопрос остался — создайте новый.")
    except Exception: pass
    await callback.message.edit_text(callback.message.text + "\n\n🔒 <b>Закрыт</b>", parse_mode="HTML")
    await callback.answer("Закрыт")

# ── Тарифы ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_tariffs")
async def admin_tariffs(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id): return
    tariffs = await dal.get_all_tariffs(session)
    await callback.message.edit_text("📦 <b>Тарифы</b>", parse_mode="HTML", reply_markup=tariff_list_kb(tariffs))

@router.callback_query(F.data.startswith("admin_tariff:"))
async def view_tariff(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id): return
    t = await dal.get_tariff(session, int(callback.data.split(":")[1]))
    if not t: await callback.answer("Не найден", show_alert=True); return
    traffic = f"{t.traffic_limit_gb} ГБ" if t.traffic_limit_gb else "Безлимит"
    await callback.message.edit_text(
        f"📦 <b>{t.name}</b>\n{t.description or ''}\n⏱ {t.duration_days} дн. | 📊 {traffic} | "
        f"📱 {t.device_limit or '∞'} уст. | 💰 {int(t.price)} ₽\n{'✅ Активен' if t.is_active else '❌ Неактивен'}",
        parse_mode="HTML", reply_markup=tariff_manage_kb(t.id, t.is_active))

@router.callback_query(F.data == "admin_create_tariff")
async def create_tariff_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await state.set_state(AdminSG.tariff_name)
    await callback.message.answer("📦 Создание тарифа\n\nВведите <b>название</b>:", parse_mode="HTML")
    await callback.answer()

@router.message(AdminSG.tariff_name)
async def tariff_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AdminSG.tariff_description)
    await message.answer("Введите <b>описание</b> (или '-' пропустить):", parse_mode="HTML")

@router.message(AdminSG.tariff_description)
async def tariff_description(message: Message, state: FSMContext):
    await state.update_data(description=None if message.text.strip() == "-" else message.text.strip())
    await state.set_state(AdminSG.tariff_days)
    await message.answer("Введите <b>количество дней</b>:", parse_mode="HTML")

@router.message(AdminSG.tariff_days)
async def tariff_days(message: Message, state: FSMContext):
    try:
        days = int(message.text.strip()); assert days > 0
    except: await message.answer("❌ Введите целое число > 0:"); return
    await state.update_data(duration_days=days)
    await state.set_state(AdminSG.tariff_traffic)
    await message.answer("Введите <b>лимит трафика ГБ</b> (0 = безлимит):", parse_mode="HTML")

@router.message(AdminSG.tariff_traffic)
async def tariff_traffic(message: Message, state: FSMContext):
    try:
        gb = int(message.text.strip()); assert gb >= 0
    except: await message.answer("❌ Введите целое число >= 0:"); return
    await state.update_data(traffic_limit_gb=gb)
    await state.set_state(AdminSG.tariff_devices)
    await message.answer("Введите <b>лимит устройств</b> (0 = безлимит):", parse_mode="HTML")

@router.message(AdminSG.tariff_devices)
async def tariff_devices(message: Message, state: FSMContext):
    try:
        d = int(message.text.strip()); assert d >= 0
    except: await message.answer("❌ Введите целое число >= 0:"); return
    await state.update_data(device_limit=d)
    await state.set_state(AdminSG.tariff_price)
    await message.answer("Введите <b>цену</b> в рублях:", parse_mode="HTML")

@router.message(AdminSG.tariff_price)
async def tariff_price(message: Message, session: AsyncSession, state: FSMContext):
    try:
        price = float(message.text.strip().replace(",",".")); assert price > 0
    except: await message.answer("❌ Введите число > 0:"); return
    data = await state.get_data()
    data["price"] = price
    t = await dal.create_tariff(session, **data)
    await state.clear()
    await message.answer(f"✅ Тариф <b>{t.name}</b> создан! {t.duration_days} дн. | {int(t.price)} ₽", parse_mode="HTML")

@router.callback_query(F.data.startswith("toggle_tariff:"))
async def toggle_tariff(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id): return
    tariff_id = int(callback.data.split(":")[1])
    t = await dal.get_tariff(session, tariff_id)
    if not t: await callback.answer("Не найден", show_alert=True); return
    await dal.update_tariff(session, tariff_id, is_active=not t.is_active)
    await callback.answer("Статус обновлён")
    await callback.message.edit_reply_markup(reply_markup=tariff_manage_kb(tariff_id, not t.is_active))

@router.callback_query(F.data.startswith("delete_tariff:"))
async def delete_tariff(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id): return
    await dal.delete_tariff(session, int(callback.data.split(":")[1]))
    tariffs = await dal.get_all_tariffs(session)
    await callback.answer("Удалён")
    await callback.message.edit_text("📦 <b>Тарифы</b>", parse_mode="HTML", reply_markup=tariff_list_kb(tariffs))

# ── Ноды ──────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_nodes")
async def admin_nodes(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    nodes = await remnawave.get_nodes()
    if not nodes:
        await callback.answer("Ноды не найдены", show_alert=True); return
    await callback.message.edit_text(f"📡 <b>Ноды ({len(nodes)})</b>",
                                     parse_mode="HTML", reply_markup=nodes_kb(nodes))

@router.callback_query(F.data.startswith("node:"))
async def view_node(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    node_uuid = callback.data.split(":", 1)[1]
    nodes = await remnawave.get_nodes()
    node = next((n for n in nodes if str(n.uuid) == node_uuid), None)
    if not node: await callback.answer("Нода не найдена", show_alert=True); return
    status = "🟢 Онлайн" if node.is_connected else "🔴 Офлайн"
    await callback.message.edit_text(
        f"📡 <b>{node.name}</b>\n\nСтатус: {status}\nАдрес: {node.address}\nUUID: <code>{node_uuid}</code>",
        parse_mode="HTML", reply_markup=node_manage_kb(node_uuid))

@router.callback_query(F.data.startswith("restart_node:"))
async def restart_node(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    node_uuid = callback.data.split(":", 1)[1]
    ok = await remnawave.restart_node(node_uuid)
    await callback.answer("🔄 Нода перезагружается..." if ok else "❌ Ошибка перезагрузки", show_alert=True)

# ── Тех. работы ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_toggle_maintenance")
async def toggle_maintenance(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id): return
    current = await dal.get_setting(session, "maintenance", "0")
    new_val = "0" if current == "1" else "1"
    await dal.set_setting(session, "maintenance", new_val)
    if new_val == "1":
        users = await dal.get_all_users(session, only_registered=True)
        sent = 0
        for u in users:
            if u.telegram_id in settings.admin_ids: continue
            try:
                await callback.bot.send_message(u.telegram_id,
                    "🔧 <b>Технические работы</b>\n\nБот временно недоступен. Приносим извинения!", parse_mode="HTML")
                sent += 1
            except: pass
        await callback.answer(f"🔴 Тех. работы. Уведомлено: {sent}", show_alert=True)
    else:
        await callback.answer("🟢 Тех. работы выключены", show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=admin_menu_kb(maintenance_on=new_val == "1"))

# ── Пользователи ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id): return
    users = await dal.get_all_users(session, only_registered=True)
    builder = InlineKeyboardBuilder()
    for u in users[:20]:
        builder.button(text=f"@{u.username or '—'} | {u.remnawave_username or '?'}",
                       callback_data=f"admin_user:{u.telegram_id}")
    builder.button(text="◀️ Назад", callback_data="admin_menu")
    builder.adjust(1)
    await callback.message.edit_text(f"👥 <b>Пользователи ({len(users)})</b>",
                                     parse_mode="HTML", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("admin_user:"))
async def view_user(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id): return
    tg_id = int(callback.data.split(":")[1])
    user = await dal.get_user(session, tg_id)
    if not user: await callback.answer("Не найден", show_alert=True); return
    sub_info = "—"
    if user.remnawave_uuid:
        try:
            rw = await remnawave.get_subscription_info(user.remnawave_uuid)
            if rw: sub_info = f"{rw.status.value} до {rw.expire_at.strftime('%d.%m.%Y')}"
        except: pass
    await callback.message.edit_text(
        f"👤 TG: <code>{tg_id}</code> | @{user.username or '—'}\n"
        f"Аккаунт: <code>{user.remnawave_username or '—'}</code>\n"
        f"Подписка: {sub_info}\nБан: {'🚫' if user.is_banned else '✅'}\n"
        f"С: {user.created_at.strftime('%d.%m.%Y')}",
        parse_mode="HTML", reply_markup=user_manage_kb(tg_id, user.is_banned))

@router.callback_query(F.data.startswith("toggle_ban:"))
async def toggle_ban(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id): return
    tg_id = int(callback.data.split(":")[1])
    user = await dal.get_user(session, tg_id)
    if not user: await callback.answer("Не найден", show_alert=True); return
    new_ban = not user.is_banned
    await dal.update_user(session, tg_id, is_banned=new_ban)
    await callback.answer(f"{'🚫 Забанен' if new_ban else '✅ Разбанен'}", show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=user_manage_kb(tg_id, new_ban))

# ── Рассылка ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    await callback.message.edit_text("📢 <b>Рассылка</b>\n\nВыберите аудиторию:",
                                     parse_mode="HTML", reply_markup=broadcast_target_kb())

@router.callback_query(F.data.startswith("broadcast:"))
async def broadcast_target_cb(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await state.set_state(AdminSG.broadcast_text)
    await state.update_data(broadcast_target=callback.data.split(":")[1])
    await callback.message.answer("✏️ Введите текст рассылки (поддерживается HTML):")
    await callback.answer()

@router.message(AdminSG.broadcast_text)
async def send_broadcast(message: Message, session: AsyncSession, state: FSMContext):
    if not is_admin(message.from_user.id): return
    data = await state.get_data()
    target = data.get("broadcast_target", "all")
    users = await dal.get_all_users(session, only_registered=True)
    sent = failed = 0
    for u in users:
        if u.telegram_id in settings.admin_ids: continue
        if target in ("active", "expired") and u.remnawave_uuid:
            try:
                rw = await remnawave.get_subscription_info(u.remnawave_uuid)
                status = rw.status.value if rw else ""
                if target == "active" and status != "ACTIVE": continue
                if target == "expired" and status == "ACTIVE": continue
            except: continue
        try:
            await message.bot.send_message(u.telegram_id, message.text, parse_mode="HTML")
            sent += 1
        except: failed += 1
    await state.clear()
    await message.answer(f"📢 Готово. ✅ {sent} | ❌ {failed}")
