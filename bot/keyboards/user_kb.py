from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from db.models import Tariff
from config.settings import settings


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Личный кабинет"), KeyboardButton(text="🛒 Купить подписку")],
            [KeyboardButton(text="💬 Поддержка"), KeyboardButton(text="👥 Пригласить друга")],
        ],
        resize_keyboard=True,
    )


def back_kb(callback_data: str = "back_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=callback_data)]
    ])


def tariffs_kb(tariffs: list[Tariff]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for t in tariffs:
        traffic = f"{t.traffic_limit_gb} ГБ" if t.traffic_limit_gb else "∞ безлимит"
        devices = f"{t.device_limit} уст." if t.device_limit else "∞ уст."
        label = f"{t.name} — {int(t.price)} ₽ | {t.duration_days}д | {traffic} | {devices}"
        builder.button(text=label, callback_data=f"tariff:{t.id}")
    builder.button(text="◀️ Назад", callback_data="back_main")
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
        if settings.DEVICE_SLOT_PRICE > 0:
            builder.button(text="➕ Добавить устройство", callback_data="buy_device_slot")
    builder.button(text="💳 История платежей", callback_data="payment_history")
    builder.button(text="◀️ Назад", callback_data="back_main")
    builder.adjust(1)
    return builder.as_markup()


def proxy_kb(proxy_url: str) -> InlineKeyboardMarkup:
    """Inline-кнопка которая открывает tg://proxy прямо в Telegram."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Подключить прокси", url=proxy_url)],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])


def subscription_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Продлить подписку", callback_data="renew_subscription")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_profile")],
    ])
