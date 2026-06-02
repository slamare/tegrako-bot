from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.states.states import PaymentSG
from bot.keyboards.user_kb import tariffs_kb, requisites_kb, cancel_kb
from config.settings import settings
from db import dal

router = Router()


async def _get_tariffs_for_user(session: AsyncSession, user) -> list:
    """
    Возвращает список тарифов с учётом статуса пользователя:

    - is_trial:    только новорегам без подписки и без одобренных платежей
    - is_referral: только рефералам без одобренных платежей
    - обычный:     всем, кроме рефералов у которых ещё не было ни одного платежа
                   (им показывается реферальный тариф вместо обычного)
    """
    all_tariffs = await dal.get_active_tariffs(session)

    has_payment = await dal.has_any_approved_payment(session, user.id)
    has_sub = bool(user.remnawave_uuid)
    is_referral = bool(user.referred_by)
    used_referral = await dal.has_used_referral_tariff(session, user.id)

    # Реферал на первом месяце — ещё не платил
    is_first_month_referral = is_referral and not has_payment and not used_referral

    result = []
    for t in all_tariffs:
        if t.is_trial:
            # Только новорегам: нет подписки и нет ни одного платежа
            if not has_sub and not has_payment:
                result.append(t)
        elif t.is_referral:
            # Только рефералам на первом месяце
            if is_first_month_referral:
                result.append(t)
        else:
            # Обычный тариф — всем кроме рефералов на первом месяце
            if not is_first_month_referral:
                result.append(t)

    return result


@router.message(F.text == "🛒 Купить подписку")
async def buy_subscription(message: Message, session: AsyncSession, state: FSMContext):
    user = await dal.get_user(session, message.from_user.id)
    if not user or not user.is_registered:
        await message.answer("Сначала завершите регистрацию — нажмите /start")
        return

    tariffs = await _get_tariffs_for_user(session, user)
    if not tariffs:
        await message.answer("😔 Тарифы временно недоступны. Попробуйте позже.")
        return

    await message.answer(
        "📦 <b>Выберите тариф:</b>",
        parse_mode="HTML",
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

    # Проверка триала
    if tariff.is_trial:
        if user and await dal.has_used_trial(session, user.id):
            await callback.answer(
                "🚫 Пробный тариф доступен только новым пользователям без подписки.",
                show_alert=True,
            )
            return

    # Проверка реферального тарифа
    if tariff.is_referral:
        has_payment = await dal.has_any_approved_payment(session, user.id)
        used_referral = await dal.has_used_referral_tariff(session, user.id)
        if not user.referred_by or has_payment or used_referral:
            await callback.answer(
                "🚫 Реферальный тариф доступен только приглашённым пользователям на первый месяц.",
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

    text = (
        f"📦 <b>{tariff.name}</b>\n\n"
        f"⏱ Срок: {tariff.duration_days} дней\n"
        f"📊 Трафик: {traffic}\n"
        f"📱 Устройств: {devices}\n"
        f"💰 Стоимость: <b>{int(tariff.price)} ₽</b>\n\n"
        f"Выберите способ оплаты:"
    )

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=requisites_kb(requisites))
    await state.set_state(PaymentSG.choose_requisite)


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
    is_image = (
        details.lower().endswith((".png", ".jpg", ".jpeg"))
        or details.startswith("AgAC")
    )

    caption_qr = (
        f"💳 <b>Оплата через {req['label']}</b>\n\n"
        f"Отсканируйте QR-код в приложении банка.\n\n"
        f"📌 В комментарии укажите ваш ID: <code>{callback.from_user.id}</code>\n\n"
        f"После оплаты пришлите <b>скриншот</b> подтверждения."
    )

    caption_text = (
        f"💳 <b>Оплата через {req['label']}</b>\n\n"
        f"Переведите <b>{int(data['amount'])} ₽</b> по реквизитам:\n\n"
        f"<code>{details}</code>\n\n"
        f"📌 В комментарии укажите ваш ID: <code>{callback.from_user.id}</code>\n\n"
        f"После оплаты пришлите <b>скриншот</b> подтверждения."
    )

    if is_image:
        await callback.message.delete()
        await callback.message.answer_photo(
            details,
            caption=caption_qr,
            parse_mode="HTML",
            reply_markup=cancel_kb(),
        )
    else:
        await callback.message.edit_text(caption_text, parse_mode="HTML", reply_markup=cancel_kb())

    await state.set_state(PaymentSG.waiting_screenshot)


@router.message(PaymentSG.waiting_screenshot, F.photo)
async def receive_screenshot(message: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    user = await dal.get_user(session, message.from_user.id)
    tariff = await dal.get_tariff(session, data["tariff_id"])

    if not tariff or not user:
        await message.answer("Произошла ошибка. Попробуйте снова.")
        await state.clear()
        return

    file_id = message.photo[-1].file_id
    payment_method = data.get("payment_method", "—")

    payment = await dal.create_payment(
        session,
        user_id=user.id,
        tariff_id=tariff.id,
        amount=float(tariff.price),
        payment_method=payment_method,
        screenshot_file_id=file_id,
    )

    trial_mark = " 🎁" if tariff.is_trial else ""
    ref_mark = " 👥" if tariff.is_referral else ""

    admin_text = (
        f"💳 <b>Новая оплата #{payment.id}</b>\n\n"
        f"👤 Пользователь: @{user.username or '—'} (<code>{user.telegram_id}</code>)\n"
        f"🆔 Аккаунт: <code>{user.remnawave_username or '—'}</code>\n"
        f"📦 Тариф: {tariff.name}{trial_mark}{ref_mark} ({tariff.duration_days} дн.)\n"
        f"💰 Сумма: {int(tariff.price)} ₽\n"
        f"💳 Метод: {payment_method}"
    )

    from bot.keyboards.admin_kb import payment_approve_kb

    for admin_id in settings.admin_ids:
        try:
            admin_msg = await message.bot.send_photo(
                chat_id=admin_id,
                photo=file_id,
                caption=admin_text,
                parse_mode="HTML",
                reply_markup=payment_approve_kb(payment.id),
            )
            await dal.update_payment(session, payment.id, admin_message_id=admin_msg.message_id)
        except Exception:
            pass

    await message.answer(
        "✅ <b>Скриншот получен!</b>\n\n"
        "Ваш платёж отправлен на проверку. Обычно это занимает до 30 минут.\n"
        "После подтверждения вы получите уведомление.",
        parse_mode="HTML",
        reply_markup=__import__('bot.keyboards.user_kb', fromlist=['main_menu_kb']).main_menu_kb(),
    )
    await state.clear()


@router.message(PaymentSG.waiting_screenshot)
async def wrong_screenshot_format(message: Message):
    await message.answer("📸 Пожалуйста, пришлите именно <b>фото</b> скриншота.", parse_mode="HTML")


# ── Кнопка продления из ЛК ───────────────────────────────────────────────────

@router.callback_query(F.data == "renew_subscription")
async def renew_subscription(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    user = await dal.get_user(session, callback.from_user.id)
    tariffs = await _get_tariffs_for_user(session, user)
    if not tariffs:
        await callback.answer("Тарифы временно недоступны", show_alert=True)
        return

    await callback.message.edit_text(
        "📦 <b>Выберите тариф для продления:</b>",
        parse_mode="HTML",
        reply_markup=tariffs_kb(tariffs),
    )
    await state.set_state(PaymentSG.choose_tariff)


# ── Навигация ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "back_tariffs")
async def back_to_tariffs(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    user = await dal.get_user(session, callback.from_user.id)
    tariffs = await _get_tariffs_for_user(session, user)
    await callback.message.edit_text(
        "📦 <b>Выберите тариф:</b>",
        parse_mode="HTML",
        reply_markup=tariffs_kb(tariffs),
    )
    await state.set_state(PaymentSG.choose_tariff)
