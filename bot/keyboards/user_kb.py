from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from db.models import Tariff
from config.settings import settings


def main_menu_kb(is_admin: bool = False, show_proxy: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="👤 Личный кабинет"), KeyboardButton(text="🛒 Купить подписку")],
        [KeyboardButton(text="💬 Поддержка"), KeyboardButton(text="👥 Пригласить друга")],
    ]
    if show_proxy:
        rows.append([KeyboardButton(text="📡 Proxy для Telegram")])
    if is_admin:
        rows.append([KeyboardButton(text="⚙️ Администратор")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def back_kb(callback_data: str = "back_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=callback_data)]
    ])


def back_home_kb(back_cb: str) -> InlineKeyboardMarkup:
    """Кнопки Назад + Главное меню."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="◀️ Назад", callback_data=back_cb),
            InlineKeyboardButton(text="🏠 Меню", callback_data="back_main"),
        ]
    ])


def tariffs_kb(tariffs: list[Tariff]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for t in tariffs:
        traffic = f"{t.traffic_limit_gb} ГБ" if t.traffic_limit_gb else "∞ безлимит"
        devices = f"{t.device_limit} уст." if t.device_limit else "∞ уст."
        label = f"{t.name} — {int(t.price)} ₽ | {t.duration_days}д | {traffic} | {devices}"
        builder.button(text=label, callback_data=f"tariff:{t.id}")
    builder.button(text="🏠 Меню", callback_data="back_main")
    builder.adjust(1)
    return builder.as_markup()


def requisites_kb(requisites: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, req in enumerate(requisites):
        builder.button(text=req["label"], callback_data=f"req:{i}")
    builder.button(text="◀️ Назад", callback_data="back_tariffs")
    builder.adjust(1)
    return builder.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])


def profile_kb(has_subscription: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_subscription:
        builder.button(text="📋 Моя подписка", callback_data="my_subscription")
        builder.button(text="📱 Мои устройства", callback_data="my_devices")
    builder.button(text="💳 История платежей", callback_data="payment_history")
    builder.button(text="🏠 Меню", callback_data="back_main")
    builder.adjust(1)
    return builder.as_markup()


def devices_kb(devices: list, limit: int, show_buy_slot: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура раздела устройств — удаление + кнопка покупки слота если включена."""
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
        builder.button(text="➕ Добавить устройство", callback_data="buy_device_slot")
    builder.button(text="◀️ Назад", callback_data="back_profile")
    builder.button(text="🏠 Меню", callback_data="back_main")
    builder.adjust(1)
    return builder.as_markup()


def proxy_kb(proxy_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Подключить прокси", url=proxy_url)],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="back_main")],
    ])


def subscription_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Продлить подписку", callback_data="renew_subscription")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_profile")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="back_main")],
    ])
