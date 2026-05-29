from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.states.states import SupportSG
from bot.keyboards.user_kb import back_kb, main_menu_kb
from config.settings import settings
from db import dal

router = Router()


@router.message(F.text == "💬 Поддержка")
async def open_support(message: Message, session: AsyncSession, state: FSMContext):
    user = await dal.get_user(session, message.from_user.id)
    if not user or not user.is_registered:
        await message.answer("Сначала зарегистрируйтесь — нажмите /start")
        return

    # Проверяем открытый тикет
    ticket = await dal.get_open_ticket(session, user.id)
    if ticket:
        await message.answer(
            f"💬 <b>Поддержка</b>\n\n"
            f"У вас уже есть открытый тикет #{ticket.id}.\n"
            f"Напишите ваш вопрос, и мы ответим как можно скорее.\n\n"
            f"Чтобы закончить диалог, напишите /close",
            parse_mode="HTML",
            reply_markup=back_kb("back_main"),
        )
        await state.set_state(SupportSG.waiting_message)
        await state.update_data(ticket_id=ticket.id)
        return

    # Создаём новый тикет
    ticket = await dal.create_ticket(session, user.id)
    await message.answer(
        f"💬 <b>Поддержка</b>\n\n"
        f"Тикет #{ticket.id} открыт.\n"
        f"Опишите вашу проблему, и мы ответим как можно скорее.\n\n"
        f"Чтобы закончить диалог, напишите /close",
        parse_mode="HTML",
        reply_markup=back_kb("back_main"),
    )
    await state.set_state(SupportSG.waiting_message)
    await state.update_data(ticket_id=ticket.id)


@router.message(SupportSG.waiting_message, F.text == "/close")
async def close_ticket_by_user(message: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    if ticket_id:
        await dal.close_ticket(session, ticket_id)
        # Уведомляем админов
        for admin_id in settings.admin_ids:
            try:
                await message.bot.send_message(
                    admin_id,
                    f"🔒 Тикет #{ticket_id} закрыт пользователем."
                )
            except Exception:
                pass

    await state.clear()
    await message.answer("✅ Тикет закрыт. Если появятся вопросы — напишите снова.", reply_markup=main_menu_kb())


@router.message(SupportSG.waiting_message)
async def user_support_message(message: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("ticket_id")

    if not ticket_id:
        await state.clear()
        return

    # Определяем тип медиа
    media_file_id = None
    media_type = None
    text = message.text or message.caption

    if message.photo:
        media_file_id = message.photo[-1].file_id
        media_type = "photo"
    elif message.document:
        media_file_id = message.document.file_id
        media_type = "document"
    elif message.video:
        media_file_id = message.video.file_id
        media_type = "video"

    # Сохраняем сообщение
    await dal.add_ticket_message(
        session,
        ticket_id=ticket_id,
        sender_role="user",
        sender_tg_id=message.from_user.id,
        text=text,
        media_file_id=media_file_id,
        media_type=media_type,
        tg_message_id=message.message_id,
    )

    user = await dal.get_user(session, message.from_user.id)
    ticket = await dal.get_ticket_by_id(session, ticket_id)

    from bot.keyboards.admin_kb import ticket_reply_kb

    # Форвардим администраторам
    notify_text = (
        f"💬 <b>Тикет #{ticket_id}</b>\n"
        f"От: @{user.username or '—'} (<code>{user.telegram_id}</code>)\n"
        f"Аккаунт: <code>{user.remnawave_username or '—'}</code>"
    )

    for admin_id in settings.admin_ids:
        try:
            await message.bot.send_message(
                admin_id,
                notify_text,
                parse_mode="HTML",
            )
            # Форвардим само сообщение
            await message.forward(admin_id)
            # Кнопки для ответа
            await message.bot.send_message(
                admin_id,
                "👆 Сообщение от пользователя:",
                reply_markup=ticket_reply_kb(ticket_id),
            )
        except Exception:
            pass

    await message.reply("✅ Сообщение отправлено в поддержку.")
