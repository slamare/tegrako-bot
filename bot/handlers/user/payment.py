import asyncio

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards.admin_kb import payment_approve_kb
from bot.keyboards.user_kb import tariffs_kb, cancel_kb, main_menu_kb
from bot.states.states import PaymentSG
from bot.utils.helpers import edit_or_answer, cleanup_fsm_interaction, delete_later
from config.settings import settings
from db import dal

router = Router()


async def _get_tariffs_for_user(session, user) -> list:
    all_tariffs = await dal.get_active_tariffs(session)
    has_payment = await dal.has_any_approved_payment(session, user.id)
    has_sub = bool(user.remnawave_uuid)
    is_referral = bool(user.referred_by)
    used_referral = await dal.has_used_referral_tariff(session, user.id)
    is_first_month_referral = is_referral and not has_payment and not used_referral
    result = []
    for t in all_tariffs:
        if t.is_trial:
            if not has_sub and not has_payment:
                result.append(t)
        elif t.is_referral:
            if is_first_month_referral:
                result.append(t)
        else:
            if not is_first_month_referral:
                result.append(t)
    return result


async def _check_purchase_access(session, tg_id: int) -> tuple[bool, str]:
    if tg_id in settings.admin_ids:
        return True, ""
    mode = await dal.get_setting(session, "access_mode", "open")
    if mode in ("closed", "no_purchase"):
        return False, "🚫 Оформление подписки временно недоступно."
    return True, ""


def _requisites_kb(requisites: list, back_cb: str = "back_to_tariff_detail") -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=req["label"], callback_data=f"req:{i}")] for i, req in enumerate(requisites)]
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _tariff_card(session, state: FSMContext) -> tuple[str | None, InlineKeyboardMarkup | None]:
    data = await state.get_data()
    tariff_id = data.get("tariff_id")
    if not tariff_id:
        return None, None
    tariff = await dal.get_tariff(session, tariff_id)
    if not tariff:
        return None, None

    amount = data.get("amount", float(tariff.price))
    traffic = f"{tariff.traffic_limit_gb} ГБ" if tariff.traffic_limit_gb else "Безлимит"
    devices = f"{tariff.device_limit} уст." if tariff.device_limit else "Безлимит"
    promo_code = data.get("promo_code")

    if promo_code:
        base_price = float(tariff.price)
        discount = base_price - amount
        text = (
            f"📦 <b>{tariff.name}</b>\n\n"
            f"⏱ Срок: {tariff.duration_days} дней\n"
            f"📊 Трафик: {traffic}\n"
            f"📱 Устройства: {devices}\n\n"
            f"{int(base_price)} ₽\n"
            f"− {int(discount)} ₽\n"
            f"💰 Итого: <b>{int(amount)} ₽</b>\n"
            f"🎟 Промокод: <b>{promo_code}</b>"
        )
    else:
        text = (
            f"📦 <b>{tariff.name}</b>\n\n"
            f"💰 <b>{int(amount)} ₽</b>\n"
            f"⏱ Срок: {tariff.duration_days} дней\n"
            f"📊 Трафик: {traffic}\n"
            f"📱 Устройства: {devices}"
        )

    builder = InlineKeyboardBuilder()
    requisites = settings.payment_requisites
    pay_label = f"💳 Оплатить через {requisites[0]['label']}" if len(requisites) == 1 else f"💳 Оплатить · {int(amount)} ₽"
    builder.button(text=pay_label, callback_data="proceed_to_payment")
    if not promo_code:
        builder.button(text="🎟 Ввести промокод", callback_data="enter_promo")
    builder.button(text="◀️ Изменить тариф", callback_data="menu_buy")
    builder.adjust(1)
    return text, builder.as_markup()


async def _send_payment_screen(callback: CallbackQuery, state: FSMContext, req_index: int):
    requisites = settings.payment_requisites
    if req_index >= len(requisites):
        await callback.answer("Реквизит не найден", show_alert=True)
        return
    req = requisites[req_index]
    data = await state.get_data()
    await state.update_data(payment_method=req["label"])
    details = req["details"]
    is_image = details.lower().endswith((".png", ".jpg", ".jpeg")) or details.startswith("AgAC")
    promo_note = f"\n🎟 Промокод: <b>{data['promo_code']}</b>" if data.get("promo_code") else ""
    amount_str = int(data["amount"])
    back_cb = "my_devices" if data.get("payment_type") == "device_slot" else "back_to_tariff_detail"
    nav = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=back_cb)],
    ])

    if is_image:
        caption = (
            f"💳 <b>Оплата через {req['label']}</b>\n\n"
            f"Сумма: <b>{amount_str} ₽</b>\n\n"
            f"Отсканируйте QR-код в приложении банка.\n\n"
            f"📌 В комментарии укажите ID: <code>{callback.from_user.id}</code>{promo_note}\n\n"
            f"После оплаты отправьте сюда <b>скриншот</b> подтверждения."
        )
        try:
            await callback.message.delete()
        except Exception:
            pass
        msg = await callback.message.answer_photo(details, caption=caption, parse_mode="HTML", reply_markup=nav)
        await state.update_data(bot_prompt_msg_id=msg.message_id)
    else:
        caption = (
            f"💳 <b>Оплата через {req['label']}</b>\n\n"
            f"Сумма: <b>{amount_str} ₽</b>\n\n"
            f"Переведите по реквизитам:\n<code>{details}</code>\n\n"
            f"📌 В комментарии укажите ID: <code>{callback.from_user.id}</code>{promo_note}\n\n"
            f"После оплаты отправьте сюда <b>скриншот</b> подтверждения."
        )
        msg = await edit_or_answer(callback, caption, reply_markup=nav)
        await state.update_data(bot_prompt_msg_id=msg.message_id if msg else None)

    await state.set_state(PaymentSG.waiting_screenshot)


# ── Покупка ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_buy")
async def menu_buy(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    user = await dal.get_user(session, callback.from_user.id)
    if not user or not user.is_registered:
        await callback.answer("Сначала зарегистрируйтесь — /start", show_alert=True)
        return
    allowed, error = await _check_purchase_access(session, callback.from_user.id)
    if not allowed:
        await callback.answer(error, show_alert=True)
        return
    tariffs = await _get_tariffs_for_user(session, user)
    if not tariffs:
        await callback.answer("Тарифы временно недоступны.", show_alert=True)
        return
    await edit_or_answer(callback, "📦 <b>Выберите тариф:</b>", reply_markup=tariffs_kb(tariffs))
    await state.set_state(PaymentSG.choose_tariff)


@router.callback_query(PaymentSG.choose_tariff, F.data.startswith("tariff:"))
async def choose_tariff(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    tariff_id = int(callback.data.split(":")[1])
    tariff = await dal.get_tariff(session, tariff_id)
    if not tariff or not tariff.is_active:
        await callback.answer("Тариф недоступен", show_alert=True)
        return
    user = await dal.get_user(session, callback.from_user.id)
    if tariff.is_trial and user and await dal.has_used_trial(session, user.id):
        await callback.answer("Пробный тариф — только для новых пользователей без подписки.", show_alert=True)
        return
    if tariff.is_referral:
        if (not user.referred_by
                or await dal.has_any_approved_payment(session, user.id)
                or await dal.has_used_referral_tariff(session, user.id)):
            await callback.answer("🚫 Реферальный тариф — только для приглашённых на первый месяц.", show_alert=True)
            return
    if not settings.payment_requisites:
        await callback.answer("Реквизиты не настроены. Обратитесь к администратору.", show_alert=True)
        return

    await state.update_data(tariff_id=tariff_id, amount=float(tariff.price), promo_id=None, promo_code=None)
    await state.set_state(PaymentSG.tariff_detail)
    text, kb = await _tariff_card(session, state)
    await edit_or_answer(callback, text, reply_markup=kb)


@router.callback_query(F.data == "back_to_tariff_detail")
async def back_to_tariff_detail(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    text, kb = await _tariff_card(session, state)
    if not text:
        await menu_buy(callback, session, state)
        return
    await state.set_state(PaymentSG.tariff_detail)
    await edit_or_answer(callback, text, reply_markup=kb)


@router.callback_query(PaymentSG.tariff_detail, F.data == "proceed_to_payment")
async def proceed_to_payment(callback: CallbackQuery, state: FSMContext):
    requisites = settings.payment_requisites
    if not requisites:
        await callback.answer("Реквизиты не настроены.", show_alert=True)
        return
    await state.set_state(PaymentSG.choose_requisite)
    if len(requisites) == 1:
        await _send_payment_screen(callback, state, 0)
        return
    await edit_or_answer(callback, "💳 <b>Выберите способ оплаты:</b>", reply_markup=_requisites_kb(requisites))


# ── Промокод ──────────────────────────────────────────────────────────────────

@router.callback_query(PaymentSG.tariff_detail, F.data == "enter_promo")
async def enter_promo_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PaymentSG.enter_promo)
    await edit_or_answer(callback, "🎟 Введите промокод:", reply_markup=cancel_kb("back_to_tariff_detail"))


@router.message(PaymentSG.enter_promo, F.text)
async def apply_promo(message: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    promo, error = await dal.validate_promo(session, message.text.strip(), data.get("tariff_id"))
    await cleanup_fsm_interaction(message, state)
    if error:
        msg = await message.answer(f"❌ {error}", disable_notification=True)
        asyncio.create_task(delete_later(message.bot, message.chat.id, msg.message_id, 30))
        return
    new_amount = dal.apply_promo_discount(promo, data.get("amount", 0))
    await state.update_data(amount=new_amount, promo_id=promo.id, promo_code=promo.code)
    await state.set_state(PaymentSG.tariff_detail)
    text, kb = await _tariff_card(session, state)
    await message.answer(text, parse_mode="HTML", disable_notification=True, reply_markup=kb)


# ── Реквизиты ─────────────────────────────────────────────────────────────────

@router.callback_query(PaymentSG.choose_requisite, F.data.startswith("req:"))
async def choose_requisite(callback: CallbackQuery, state: FSMContext):
    req_index = int(callback.data.split(":")[1])
    await _send_payment_screen(callback, state, req_index)
    await callback.answer()


# ── Скриншот ──────────────────────────────────────────────────────────────────

@router.message(PaymentSG.waiting_screenshot, F.photo)
async def receive_screenshot(message: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    user = await dal.get_user(session, message.from_user.id)
    if not user:
        await state.clear()
        return
    tariff = await dal.get_tariff(session, data["tariff_id"]) if data.get("tariff_id") else None
    file_id = message.photo[-1].file_id
    amount = data.get("amount", float(tariff.price) if tariff else 0)
    payment = await dal.create_payment(
        session,
        user_id=user.id,
        tariff_id=tariff.id if tariff else None,
        amount=amount,
        payment_method=data.get("payment_method", "—"),
        screenshot_file_id=file_id,
        payment_type=data.get("payment_type", "subscription"),
        promo_id=data.get("promo_id"),
    )
    promo_note = f"\n🎟 {data['promo_code']}" if data.get("promo_code") else ""
    admin_text = (
        f"💳 <b>Оплата #{payment.id}</b>\n\n"
        f"👤 @{user.username or '—'} (<code>{user.telegram_id}</code>)\n"
        f"🆔 <code>{user.remnawave_username or '—'}</code>\n"
        f"📦 {tariff.name if tariff else 'доп. устройство'}"
        f"{' (' + str(tariff.duration_days) + ' дн.)' if tariff else ''}\n"
        f"💰 {int(amount)} ₽ | {data.get('payment_method', '—')}{promo_note}"
    )
    for admin_id in settings.admin_ids:
        try:
            admin_msg = await message.bot.send_photo(
                admin_id, photo=file_id, caption=admin_text,
                parse_mode="HTML", reply_markup=payment_approve_kb(payment.id),
            )
            await dal.update_payment(session, payment.id, admin_message_id=admin_msg.message_id)
        except Exception:
            pass
    await cleanup_fsm_interaction(message, state)
    await state.clear()
    msg = await message.answer(
        f"✅ <b>Платёж получен</b>\n\n"
        f"Сумма: {int(amount)} ₽\n"
        f"Тариф: {tariff.name if tariff else 'доп. устройство'}\n\n"
        f"Статус:\n🟡 Ожидает проверки\n\n"
        f"После подтверждения подписка будет активирована.",
        parse_mode="HTML",
        disable_notification=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
        ]),
    )


@router.message(PaymentSG.waiting_screenshot, F.text)
async def wrong_format_text(message: Message):
    try:
        await message.delete()
    except Exception:
        pass
    msg = await message.answer(
        "💬 <b>На этом шаге нужно отправить фото скриншота.</b>\n\nЕсли возникли вопросы — обратитесь в поддержку.",
        parse_mode="HTML",
        disable_notification=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Написать в поддержку", callback_data="menu_support")]
        ]),
    )
    asyncio.create_task(delete_later(message.bot, message.chat.id, msg.message_id, 30))


@router.message(PaymentSG.waiting_screenshot)
async def wrong_format(message: Message):
    try:
        await message.delete()
    except Exception:
        pass
    if message.voice or message.video_note:
        text = "🎙 Голосовые и кружки не принимаются.\n\nОтправьте <b>фото</b> скриншота."
    elif message.sticker or message.animation:
        text = "🙅 Стикеры и гифки не принимаются.\n\nОтправьте <b>фото</b> скриншота."
    else:
        text = "📸 <b>Ожидается фото скриншота.</b>\n\nОтправьте изображение из галереи."
    msg = await message.answer(
        text, parse_mode="HTML", disable_notification=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Написать в поддержку", callback_data="menu_support")]
        ]),
    )
    asyncio.create_task(delete_later(message.bot, message.chat.id, msg.message_id, 30))


# ── Продление / слот ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "renew_subscription")
async def renew_subscription(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    user = await dal.get_user(session, callback.from_user.id)
    allowed, error = await _check_purchase_access(session, callback.from_user.id)
    if not allowed:
        await callback.answer(error, show_alert=True)
        return
    tariffs = await _get_tariffs_for_user(session, user)
    if not tariffs:
        await callback.answer("Тарифы временно недоступны", show_alert=True)
        return
    await edit_or_answer(callback, "📦 <b>Выберите тариф:</b>", reply_markup=tariffs_kb(tariffs))
    await state.set_state(PaymentSG.choose_tariff)


@router.callback_query(F.data == "buy_device_slot")
async def buy_device_slot(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    if settings.DEVICE_SLOT_PRICE <= 0:
        await callback.answer("Эта опция сейчас недоступна.", show_alert=True)
        return
    user = await dal.get_user(session, callback.from_user.id)
    if not user or not user.remnawave_uuid:
        await callback.answer("Сначала оформите подписку.", show_alert=True)
        return
    allowed, error = await _check_purchase_access(session, callback.from_user.id)
    if not allowed:
        await callback.answer(error, show_alert=True)
        return
    requisites = settings.payment_requisites
    if not requisites:
        await callback.answer("Реквизиты не настроены.", show_alert=True)
        return
    await state.update_data(tariff_id=None, amount=settings.DEVICE_SLOT_PRICE, payment_type="device_slot",
                             promo_id=None, promo_code=None)
    await state.set_state(PaymentSG.choose_requisite)
    if len(requisites) == 1:
        await _send_payment_screen(callback, state, 0)
        return
    await edit_or_answer(
        callback,
        f"📱 <b>Дополнительный слот устройства</b>\n\nСтоимость: <b>{int(settings.DEVICE_SLOT_PRICE)} ₽</b>\n\n"
        f"После подтверждения лимит устройств увеличится на 1.\n\nВыберите способ оплаты:",
        reply_markup=_requisites_kb(requisites, back_cb="my_devices"),
    )
