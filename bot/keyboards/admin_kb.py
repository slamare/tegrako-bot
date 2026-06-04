from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from db.models import Tariff, PromoCode


def admin_menu_kb(maintenance_on: bool = False) -> InlineKeyboardMarkup:
    maint = "🟢 Выкл. тех. работы" if maintenance_on else "🔴 Вкл. тех. работы"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="💰 Ожидающие оплаты", callback_data="admin_pending_payments")],
        [InlineKeyboardButton(text="🎫 Тикеты поддержки", callback_data="admin_tickets")],
        [InlineKeyboardButton(text="📦 Тарифы", callback_data="admin_tariffs")],
        [InlineKeyboardButton(text="🎟 Промокоды", callback_data="admin_promos")],
        [InlineKeyboardButton(text="📡 Ноды", callback_data="admin_nodes")],
        [InlineKeyboardButton(text="🔌 Инбаунды / Хосты", callback_data="admin_inbounds")],
        [InlineKeyboardButton(text="🔐 Режим доступа", callback_data="admin_access_mode")],
        [InlineKeyboardButton(text="🔘 Кнопки меню", callback_data="admin_custom_buttons")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text=maint, callback_data="admin_toggle_maintenance")],
    ])


def payment_approve_kb(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"approve:{payment_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{payment_id}"),
    ]])


def ticket_reply_kb(ticket_id: int, is_closed: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if not is_closed:
        rows.append([InlineKeyboardButton(text="✉️ Ответить", callback_data=f"reply_ticket:{ticket_id}")])
        rows.append([InlineKeyboardButton(text="🔒 Закрыть тикет", callback_data=f"close_ticket:{ticket_id}")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_tickets")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tariff_list_kb(tariffs: list[Tariff]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for t in tariffs:
        status = "✅" if t.is_active else "❌"
        badge = " 🎁" if t.is_trial else (" 👥" if t.is_referral else "")
        builder.button(
            text=f"{status} {t.name} — {int(t.price)} ₽{badge}",
            callback_data=f"admin_tariff:{t.id}",
        )
    builder.button(text="➕ Создать тариф", callback_data="admin_create_tariff")
    builder.button(text="◀️ Назад", callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def tariff_manage_kb(
    tariff_id: int, is_active: bool, is_trial: bool, is_referral: bool
) -> InlineKeyboardMarkup:
    toggle_active = "❌ Деактивировать" if is_active else "✅ Активировать"
    toggle_trial = "🔓 Убрать триал" if is_trial else "🎁 Сделать триальным"
    toggle_ref = "🔓 Убрать реферальный" if is_referral else "👥 Сделать реферальным"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_active, callback_data=f"toggle_tariff:{tariff_id}")],
        [InlineKeyboardButton(text=toggle_trial, callback_data=f"toggle_trial:{tariff_id}")],
        [InlineKeyboardButton(text=toggle_ref, callback_data=f"toggle_referral:{tariff_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_tariff:{tariff_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_tariffs")],
    ])


def promo_list_kb(promos: list[PromoCode]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for p in promos:
        status = "✅" if p.is_active else "❌"
        disc = f"{p.discount_percent}%" if p.discount_percent else f"{int(p.discount_fixed)} ₽"
        builder.button(
            text=f"{status} {p.code} — {disc} ({p.used_count}/{p.max_uses})",
            callback_data=f"admin_promo:{p.id}",
        )
    builder.button(text="➕ Создать промокод", callback_data="admin_create_promo")
    builder.button(text="◀️ Назад", callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def nodes_kb(nodes: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for node in nodes:
        status = "🟢" if node.is_connected else "🔴"
        builder.button(text=f"{status} {node.name}", callback_data=f"node:{node.uuid}")
    builder.button(text="◀️ Назад", callback_data="admin_menu")
    builder.adjust(1)
    return builder.as_markup()


def node_manage_kb(node_uuid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Перезагрузить", callback_data=f"restart_node:{node_uuid}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_nodes")],
    ])


def user_manage_kb(telegram_id: int, is_banned: bool, has_sub: bool = False) -> InlineKeyboardMarkup:
    ban_text = "🔓 Разбанить" if is_banned else "🚫 Забанить"
    rows = [
        [InlineKeyboardButton(text=ban_text, callback_data=f"toggle_ban:{telegram_id}")],
        [InlineKeyboardButton(text="📦 Назначить тариф", callback_data=f"admin_assign_tariff:{telegram_id}")],
    ]
    if has_sub:
        rows.append([InlineKeyboardButton(
            text="🎁 Бессрочный доступ", callback_data=f"admin_grant_unlimited:{telegram_id}"
        )])
        rows.append([InlineKeyboardButton(
            text="📋 Управление подпиской", callback_data=f"admin_sub_manage:{telegram_id}"
        )])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_users")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def broadcast_target_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Всем", callback_data="broadcast:all")],
        [InlineKeyboardButton(text="✅ Активные подписки", callback_data="broadcast:active")],
        [InlineKeyboardButton(text="❌ Истёкшие подписки", callback_data="broadcast:expired")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")],
    ])


def access_mode_kb(current: str) -> InlineKeyboardMarkup:
    modes = [
        ("open",        "🟢 Открытый доступ"),
        ("closed",      "🔴 Полное ограничение"),
        ("invite_only", "📨 Только по приглашениям"),
        ("no_purchase", "🚫 Запрет покупок"),
        ("no_register", "🔒 Запрет регистрации"),
    ]
    rows = []
    for mode, label in modes:
        prefix = "▶️ " if mode == current else ""
        rows.append([InlineKeyboardButton(
            text=f"{prefix}{label}",
            callback_data=f"set_access_mode:{mode}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
