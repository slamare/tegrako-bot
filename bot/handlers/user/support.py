from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from bot.states.states import SupportSG
from config.settings import settings
from db import dal

router = Router()


@router.message(SupportSG.waiting_message, Command("close"))
async def close_ticket_cmd(message: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    if ticket_id:
        await dal.close_ticket(session, ticket_id)
        for admin_id in settings.admin_ids:
            try:
                await message.bot.send_message(admin_id, f"🔒 Тикет #{ticket_id} закрыт пользователем.")
            except Exception:
                pass
    await state.clear()
    await message.answer(
        "✅ Тикет закрыт.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ]),
    )


@router.message(SupportSG.waiting_message)
async def user_support_message(message: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    if not ticket_id:
        await state.clear()
        return

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
    from bot.keyboards.admin_kb import ticket_reply_kb

    notify_text = (
        f"💬 <b>Тикет #{ticket_id}</b>\n"
        f"От: @{user.username or '—'} (<code>{user.telegram_id}</code>)\n"
        f"Аккаунт: <code>{user.remnawave_username or '—'}</code>"
    )
    for admin_id in settings.admin_ids:
        try:
            await message.bot.send_message(admin_id, notify_text, parse_mode="HTML")
            await message.forward(admin_id)
            await message.bot.send_message(
                admin_id, "👆 Сообщение от пользователя:",
                reply_markup=ticket_reply_kb(ticket_id),
            )
        except Exception:
            pass

    await message.reply(
        "✅ Отправлено.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔒 Закрыть тикет", callback_data="close_my_ticket")],
        ]),
    )
