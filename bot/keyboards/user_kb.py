from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
from db.models import Tariff
from config.settings import settings

def remove_kb() -> ReplyKeyboardRemove:
    """Удаляет reply-клавиатуру"""
    return ReplyKeyboardRemove(remove_keyboard=True)

def main_menu_kb(is_admin: bool = False, has_sub: bool = False, show_proxy: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Моя подписка", callback_data="menu_profile")
    builder.button(text="🛒 Купить подписку", callback_data="menu_buy")
    builder.button(text="💬 Поддержка", callback_data="menu_support")
    builder.button(text="👥 Пригласить друга", callback_data="menu_invite")
    
    if show_proxy:
        builder.button(text="📡 Proxy для Telegram", callback_data="menu_proxy")
    
    if is_admin:
        builder.button(text="⚙️ Администратор", callback_data="admin_menu")
    
    builder.adjust(2)
    return builder.as_markup()

def back_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

def back_kb(callback_data: str = "main_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=callback_data)]
    ])

def nav_kb(back_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="◀️ Назад", callback_data=back_cb),
            InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu"),
        ]
    ])

def tariffs_kb(tariffs: list[Tariff]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for t in tariffs:
        traffic = f"{t.traffic_limit_gb} ГБ" if t.traffic_limit_gb else "∞"
        devices = f"{t.device_limit} уст." if t.device_limit else "∞ уст."
        label = f"{t.name} — {int(t.price)} ₽ | {t.duration_days}д | {traffic} | {devices}"
        builder.button(text=label, callback_data=f"tariff:{t.id}")
    builder.button(text="◀️ Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()

def payment_methods_kb(requisites: list[dict], back_cb: str = "menu_buy") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, req in enumerate(requisites):
        builder.button(text=req["label"], callback_data=f"req:{i}")
    builder.button(text="🎟 Ввести промокод", callback_data="enter_promo")
    builder.button(text="◀️ Назад", callback_data=back_cb)
    builder.button(text="🏠 Меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()

def cancel_kb(back_cb: str = "main_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=back_cb)]
    ])

def profile_kb(has_subscription: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_subscription:
        builder.button(text="📋 Моя подписка", callback_data="my_subscription")
        builder.button(text="📱 Мои устройства", callback_data="my_devices")
        builder.button(text="💳 История платежей", callback_data="payment_history")
        builder.button(text="🏠 Меню", callback_data="main_menu")
        builder.adjust(1)
    return builder.as_markup()

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
    builder.button(text="◀️ Назад", callback_data="menu_profile")
    builder.button(text="🏠 Меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()

def proxy_kb(proxy_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Подключить прокси", url=proxy_url)],
        [InlineKeyboardButton(text="🔄 Перевыпустить ссылку", callback_data="revoke_mtproxy")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")],
    ])

def subscription_detail_kb(sub_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Открыть подписку", url=sub_url)],
        [InlineKeyboardButton(text="🔄 Сбросить ссылку", callback_data="revoke_subscription")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu_profile")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")],
    ])