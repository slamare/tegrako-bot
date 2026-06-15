"""
Хендлер MTProto прокси — кнопка в ЛК пользователя.
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from bot.services import telemt
from db import dal

router = Router()


@router.callback_query(F.data == "my_mtproto")
async def my_mtproto(callback: CallbackQuery, session: AsyncSession):
    user = await dal.get_user(session, callback.from_user.id)
    if not user or not user.is_registered:
        await callback.answer("Сначала зарегистрируйтесь", show_alert=True)
        return

    if not user.remnawave_uuid:
        await callback.answer(
            "📡 MTProto прокси доступен только при активной подписке.",
            show_alert=True,
        )
        return

    if not user.mtproto_secret:
        await callback.answer(
            "📡 Прокси ещё не настроен. Попробуйте через несколько минут или обратитесь в поддержку.",
            show_alert=True,
        )
        return

    link = await telemt.get_proxy_link(user.remnawave_username)
    if not link:
        link = telemt.build_link_fallback(user.mtproto_secret)

    if not link:
        await callback.answer("Не удалось получить ссылку. Попробуйте позже.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Подключить прокси", url=link)],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_profile")],
    ])

    await callback.message.edit_text(
        "📡 <b>MTProto прокси</b>\n\n"
        "Нажмите кнопку чтобы подключить прокси в Telegram.\n\n"
        "⚠️ <b>Ссылка персональная.</b> Не передавайте её другим — "
        "при обнаружении посторонних подключений ссылка будет сброшена.\n\n"
        "🔒 Деактивируется автоматически если подписка не оплачена более 5 дней.",
        parse_mode="HTML",
        reply_markup=kb,
    )
