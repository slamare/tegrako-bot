from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
from db.models import Tariff
from config.settings import settings

def remove_kb() -> ReplyKeyboardRemove:
    """Удаляет reply-клавиатуру"""
    return ReplyKeyboardRemove(remove_keyboard=True)

def main_menu_kb(
    is_admin: bool = False,
    status: str = "NONE",
    show_proxy: bool = False,
    sub_url: str | None = None,
    is_lifetime: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if status == "ACTIVE":
        if sub_url:
            builder.button(text="🔗 Открыть подписку", url=sub_url)
        if not is_lifetime:
            builder.button(text="🔄 Продлить подписку", callback_data="renew_subscription")
        builder.button(text="📱 Устройства", callback_data="my_devices")
        if show_proxy:
            builder.button(text="📡 Telegram Proxy", callback_data="menu_proxy")
        builder.button(text="🔄 Перевыпустить ссылку", callback_data="revoke_subscription")
        builder.button(text="💳 История платежей", callback_data="payment_history")
        builder.button(text="👥 Пригласить друга", callback_data="menu_invite")
        builder.button(text="💬 Поддержка", callback_data="menu_support")
    elif status == "EXPIRED":
        builder.button(text="🔄 Продлить подписку", callback_data="renew_subscription")
        builder.button(text="💳 История платежей", callback_data="payment_history")
        builder.button(text="👥 Пригласить друга", callback_data="menu_invite")
        builder.button(text="💬 Поддержка", callback_data="menu_support")
    else:
        builder.button(text="🛒 Купить подписку", callback_data="menu_buy")
        builder.button(text="💬 Поддержка", callback_data="menu_support")

    if is_admin:
        builder.button(text="⚙️ Администратор", callback_data="admin_menu")

    builder.adjust(1, 2)
    return builder.as_markup()

def back_kb(callback_data: str = "main_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=callback_data)]
    ])

def tariffs_kb(tariffs: list[Tariff]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for t in tariffs:
        badge = "🎁 " if t.is_trial else ""
        builder.button(text=f"{badge}{t.name} · {int(t.price)} ₽", callback_data=f"tariff:{t.id}")
    builder.button(text="◀️ Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def tariffs_list_text(tariffs: list[Tariff]) -> str:
    lines = ["📦 <b>Выберите тариф:</b>\n"]
    for t in tariffs:
        badge = "🎁 " if t.is_trial else ""
        traffic = f"{t.traffic_limit_gb} ГБ" if t.traffic_limit_gb else "Безлимит"
        devices = f"{t.device_limit} уст." if t.device_limit else "∞ уст."
        lines.append(f"{badge}<b>{t.name}</b> — {int(t.price)} ₽\n{t.duration_days} дн. · {traffic} · {devices}\n")
    return "\n".join(lines)

def cancel_kb(back_cb: str = "main_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=back_cb)]
    ])

def devices_kb(devices: list, show_buy_slot: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, d in enumerate(devices, 1):
        platform = d.platform or "Неизвестно"
        builder.button(
            text=f"🗑 Удалить {i}. {platform}",
            callback_data=f"delete_device:{d.hwid}",
        )
    if devices:
        builder.button(text="🗑 Удалить все", callback_data="delete_all_devices")
    if show_buy_slot:
        builder.button(text="➕ Добавить слот устройства", callback_data="buy_device_slot")
    builder.button(text="◀️ Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()

def proxy_kb(proxy_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Подключить прокси", url=proxy_url)],
        [InlineKeyboardButton(text="🔄 Перевыпустить ссылку", callback_data="revoke_mtproxy")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")],
    ])
