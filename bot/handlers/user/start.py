from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path
import re

from bot.states.states import RegistrationSG
from bot.keyboards.user_kb import main_menu_kb, back_kb, profile_kb, subscription_kb
from bot.services import remnawave
from config.settings import settings
from db import dal

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    tg_id = message.from_user.id

    maintenance = await dal.get_setting(session, "maintenance", "0")
    if maintenance == "1" and tg_id not in settings.admin_ids:
        await message.answer("🔧 Ведутся технические работы. Пожалуйста, попробуйте позже.")
        return

    user = await dal.get_user(session, tg_id)
    if not user:
        user = await dal.create_user(session, tg_id, username=message.from_user.username)

    if message.from_user.username and user.username != message.from_user.username:
        await dal.update_user(session, tg_id, username=message.from_user.username)

    welcome_text = (
        f"👋 Добро пожаловать в <b>{settings.BOT_NAME}</b>!\n\n"
        f"Надёжный VPN с быстрыми серверами.\n"
        f"Выберите действие в меню ниже."
    )

    image_path = getattr(settings, "WELCOME_IMAGE_URL", None)

    if image_path:
        path = Path(image_path)

        if path.exists() and path.is_file():
            photo = FSInputFile(str(path))
            await message.answer_photo(
                photo=photo,
                caption=welcome_text,
                parse_mode="HTML",
                reply_markup=main_menu_kb()
            )
        else:
            await message.answer(
                welcome_text,
                parse_mode="HTML",
                reply_markup=main_menu_kb()
            )
    else:
        await message.answer(
            welcome_text,
            parse_mode="HTML",
            reply_markup=main_menu_kb()
        )

    if user.is_registered:
        return

    rw_user = await remnawave.get_user_by_telegram_id(tg_id)
    if rw_user:
        await dal.update_user(
            session,
            tg_id,
            remnawave_username=rw_user.username,
            remnawave_uuid=str(rw_user.uuid),
            is_registered=True,
        )

        await message.answer(
            f"✅ Ваш аккаунт найден: <code>{rw_user.username}</code>.\n"
            f"Добро пожаловать обратно!",
            parse_mode="HTML",
            reply_markup=main_menu_kb(),
        )
        return

    await _start_registration(message, session, state)


async def _start_registration(message: Message, session: AsyncSession, state: FSMContext):
    tg_username = message.from_user.username
    if tg_username:
        exists = await remnawave.username_exists(tg_username)
        if not exists:
            await _finish_registration(message, session, tg_username, message.from_user.id)
            return

        await message.answer(
            f"⚠️ Имя <code>@{tg_username}</code> уже занято в системе.\n\n"
            f"Введите другое имя:",
            parse_mode="HTML",
            reply_markup=back_kb("cancel"),
        )
    else:
        await message.answer(
            "👤 У вас не установлен username.\n\nВведите имя:",
            reply_markup=back_kb("cancel"),
        )

    await state.set_state(RegistrationSG.choose_username)


async def _finish_registration(message: Message, session: AsyncSession, username: str, tg_id: int):
    await dal.update_user(session, tg_id, remnawave_username=username, is_registered=True)
    await message.answer(
        f"✅ Аккаунт зарегистрирован: <code>{username}</code>.",
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )