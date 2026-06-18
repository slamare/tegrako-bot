from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from bot.states.states import PaymentSG
from bot.keyboards.user_kb import tariffs_kb, nav_kb, cancel_kb
from config.settings import settings
from db import dal

router = Router()

async def _edit_or_answer(callback: CallbackQuery, text: str, reply_markup=None, parse_mode: str = "HTML"):
    """Универсальное редактирование: edit_text для текста, удаление+новое для фото."""
    msg = callback.message
    if msg.photo:
        # Фото нельзя превратить в текст — удаляем и шлём новое
        try:
            await msg.delete()
        except Exception:
            pass
        await msg.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
    else:
        try:
            await msg.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        except Exception:
            try:
                await msg.delete()
            except Exception:
                pass
            await msg.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
    await callback.answer()

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
        return True, " "
    mode = await dal.get_setting(session, "access_mode", "open")
    if mode in ("closed", "no_purchase"):
        return False, "🚫 Оформление подписки временно недоступно."
    return True, " "

# ── Вход в покупку ────────────────────────────────────────────────────────────
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

    await _edit_or_answer(
        callback,
        "📦 <b>Выберите тариф:</b>",
        reply_markup=tariffs_kb(tariffs),
    )
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
        await callback.answer(
            "🚫 Пробный тариф — только для новых пользователей без подписки.",
            show_alert=True,
        )
        return

    if tariff.is_referral:
        has_payment = await dal.has_any_approved_payment(session, user.id)
        used_referral = await dal.has_used_referral_tariff(session, user.id)
        if not user.referred_by or has_payment or used_referral:
            await callback.answer(
                "🚫 Реферальный тариф — только для приглашённых на первый месяц.",
                show_alert=True,
            )
            return

    await state.update_data(tariff_id=tariff_id, amount=float(tariff.price))

    requisites = settings.payment_requisites
    if not requisites:
        await callback.answer("Реквизиты не настроены. Обратитесь к администратору.", show_alert=True)
        return

    traffic = f"{tariff.traffic_limit_gb} ГБ" if tariff.traffic_limit_gb else "Безлимит"
    devices = f"{tariff.device_limit} уст." if tariff.device_limit else "Безлимит"

    kb_rows = [[InlineKeyboardButton(text=req["label"], callback_data=f"req:{i}")]
               for i, req in enumerate(requisites)]
    kb_rows.append([InlineKeyboardButton(text="🎟 Ввести промокод", callback_data="enter_promo")])
    kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu_buy")])
    kb_rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")])

    await _edit_or_answer(
        callback,
        f"📦 <b>{tariff.name}</b>\n\n"
        f"⏱ {tariff.duration_days} дней · 📊 {traffic} · 📱 {devices}\n"
        f"💰 <b>{int(tariff.price)} ₽</b>\n\n"
        f"Выберите способ оплаты:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )
    await state.set_state(PaymentSG.choose_requisite)

# ── Промокод ──────────────────────────────────────────────────────────────────
@router.callback_query(PaymentSG.choose_requisite, F.data == "enter_promo")
async def enter_promo_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PaymentSG.enter_promo)
    await _edit_or_answer(
        callback,
        "🎟 Введите промокод:",
        reply_markup=cancel_kb("menu_buy"),
    )

@router.message(PaymentSG.enter_promo)
async def apply_promo(message: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    try:
        await message.delete()
    except Exception:
        pass
    promo, error = await dal.validate_promo(session, message.text.strip(), data.get("tariff_id"))
    if error:
        await message.answer(f"❌ {error}")
        return

    new_amount = await dal.apply_promo_discount(promo, data.get("amount", 0))
    await state.update_data(amount=new_amount, promo_id=promo.id, promo_code=promo.code)
    await state.set_state(PaymentSG.choose_requisite)

    disc = f"{promo.discount_percent}%" if promo.discount_percent else f"{int(promo.discount_fixed)} ₽"
    requisites = settings.payment_requisites
    kb_rows = [[InlineKeyboardButton(text=req["label"], callback_data=f"req:{i}")]
               for i, req in enumerate(requisites)]
    kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu_buy")])
    kb_rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")])

    await message.answer(
        f"✅ Промокод <b>{promo.code}</b> применён! Скидка: {disc}\n"
        f"💰 Итого: <b>{int(new_amount)} ₽</b>\n\nВыберите способ оплаты:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )

# ── Реквизиты ─────────────────────────────────────────────────────────────────
@router.callback_query(PaymentSG.choose_requisite, F.data.startswith("req:"))
async def choose_requisite(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    req_index = int(callback.data.split(":")[1])
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

    nav = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu_buy")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")],
    ])

    amount_str = int(data["amount"])
    if is_image:
        caption = (
            f"💳 <b>Оплата через {req['label']}</b>\n\n"
            f"Отсканируйте QR-код в приложении банка.\n\n"
            f"📌 В комментарии укажите ID: <code>{callback.from_user.id}</code>"
            f"{promo_note}\n\nПришлите <b>скриншот</b> подтверждения."
        )
    else:
        caption = (
            f"💳 <b>Оплата через {req['label']}</b>\n\n"
            f"Переведите <b>{amount_str} ₽</b> по реквизитам:\n\n"
            f"<code>{details}</code>\n\n"
            f"📌 В комментарии укажите ID: <code>{callback.from_user.id}</code>"
            f"{promo_note}\n\nПришлите <b>скриншот</b> подтверждения."
        )

    if is_image:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(details, caption=caption, parse_mode="HTML", reply_markup=nav)
        await callback.answer()
    else:
        await _edit_or_answer(
            callback,
            caption,
            reply_markup=nav,
        )

    await state.set_state(PaymentSG.waiting_screenshot)

# ── Скриншот ──────────────────────────────────────────────────────────────────
@router.message(PaymentSG.waiting_screenshot, F.photo)
async def receive_screenshot(message: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    user = await dal.get_user(session, message.from_user.id)
    tariff = await dal.get_tariff(session, data.get("tariff_id")) if data.get("tariff_id") else None
    if not user:
        await state.clear()
        return

    file_id = message.photo[-1].file_id
    amount = data.get("amount", float(tariff.price) if tariff else 0)
    payment_method = data.get("payment_method", "—")
    promo_id = data.get("promo_id")

    payment = await dal.create_payment(
        session,
        user_id=user.id,
        tariff_id=tariff.id if tariff else None,
        amount=amount,
        payment_method=payment_method,
        screenshot_file_id=file_id,
        promo_id=promo_id,
    )

    promo_note = f"\n🎟 {data['promo_code']}" if data.get("promo_code") else ""
    admin_text = (
        f"💳 <b>Оплата #{payment.id}</b>\n\n"
        f"👤 @{user.username or '—'} (<code>{user.telegram_id}</code>)\n"
        f"🆔 <code>{user.remnawave_username or '—'}</code>\n"
        f"📦 {tariff.name if tariff else 'доп. устройство'} "
        f"{'(' + str(tariff.duration_days) + ' дн.)' if tariff else ''}\n"
        f"💰 {int(amount)} ₽ | {payment_method}{promo_note}"
    )

    from bot.keyboards.admin_kb import payment_approve_kb
    for admin_id in settings.admin_ids:
        try:
            admin_msg = await message.bot.send_photo(
                admin_id, photo=file_id, caption=admin_text,
                parse_mode="HTML", reply_markup=payment_approve_kb(payment.id),
            )
            await dal.update_payment(session, payment.id, admin_message_id=admin_msg.message_id)
        except Exception:
            pass

    from bot.keyboards.user_kb import main_menu_kb
    await message.answer(
        "✅ <b>Скриншот получен!</b>\n\n"
        "Платёж отправлен на проверку. Обычно до 30 минут.\n"
        "После подтверждения получите уведомление.",
        parse_mode="HTML",
        reply_markup=main_menu_kb(is_admin=message.from_user.id in settings.admin_ids),
    )
    await state.clear()

@router.message(PaymentSG.waiting_screenshot)
async def wrong_format(message: Message):
    await message.answer("📸 Пришлите именно <b>фото</b> скриншота.", parse_mode="HTML")

# ── Продление из профиля ──────────────────────────────────────────────────────
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
    await _edit_or_answer(
        callback,
        "📦 <b>Выберите тариф:</b>",
        reply_markup=tariffs_kb(tariffs),
    )
    await state.set_state(PaymentSG.choose_tariff)

# ── Доп. слот устройства ──────────────────────────────────────────────────────
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

    await state.update_data(tariff_id=None, amount=settings.DEVICE_SLOT_PRICE, payment_type="device_slot")

    kb_rows = [[InlineKeyboardButton(text=req["label"], callback_data=f"req:{i}")]
               for i, req in enumerate(requisites)]
    kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="my_devices")])
    kb_rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")])

    await _edit_or_answer(
        callback,
        f"📱 <b>Дополнительный слот устройства</b>\n\n"
        f"Стоимость: <b>{int(settings.DEVICE_SLOT_PRICE)} ₽</b>\n\n"
        f"После подтверждения лимит устройств увеличится на 1.\n\n"
        f"Выберите способ оплаты:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )
    await state.set_state(PaymentSG.choose_requisite)