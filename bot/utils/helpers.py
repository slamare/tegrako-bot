"""Общие утилиты для хендлеров."""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError


async def delete_later(bot, chat_id: int, message_id: int, delay: int = 30):
    """Отложенное удаление сообщения."""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def cleanup_fsm_interaction(
    message: Message, 
    state: FSMContext, 
    final_msg: Message = None, 
    final_delay: int = 30
):
    """
    Удаляет сообщение пользователя и предыдущий промпт бота.
    Если передан final_msg — планирует его удаление через final_delay секунд.
    """
    data = await state.get_data()
    if prompt_id := data.get("bot_prompt_msg_id"):
        try:
            await message.bot.delete_message(message.chat.id, prompt_id)
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass
    
    if final_msg:
        asyncio.create_task(delete_later(message.bot, message.chat.id, final_msg.message_id, final_delay))


# ── Универсальное редактирование сообщений ─────────────────────────────────────

async def edit_or_answer(
    callback: CallbackQuery,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
):
    msg = callback.message
    edited = False
    try:
        if msg.photo:
            await msg.edit_caption(caption=text, parse_mode=parse_mode, reply_markup=reply_markup)
        else:
            await msg.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        edited = True
    except TelegramBadRequest as e:
        error_msg = str(e).lower()
        if "message is not modified" in error_msg:
            edited = True
        else:
            try: await msg.delete()
            except: pass
            await msg.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
            edited = True
    except TelegramAPIError:
        try: await msg.delete()
        except: pass
        await msg.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
        edited = True
    except Exception:
        try: await msg.delete()
        except: pass
        await msg.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
        edited = True

    try: await callback.answer()
    except: pass
    return edited


# ── Кэш последних сообщений меню ─────────────────────────────────────────────

class _MenuMessageCache:
    def __init__(self, ttl: int = 600):
        self._ttl = ttl
        self._data: dict[int, tuple[int, float]] = {}

    def get(self, tg_id: int) -> Optional[int]:
        entry = self._data.get(tg_id)
        if not entry: return None
        msg_id, ts = entry
        if time.time() - ts > self._ttl:
            del self._data[tg_id]
            return None
        return msg_id

    def set(self, tg_id: int, message_id: int):
        self._data[tg_id] = (message_id, time.time())

    def delete(self, tg_id: int):
        self._data.pop(tg_id, None)

menu_cache = _MenuMessageCache(ttl=600)

async def show_menu_message(
    target,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
    photo_url: str | None = None,
) -> Message:
    tg_id = target.from_user.id
    
    if isinstance(target, CallbackQuery):
        msg = target.message
        try: await msg.delete()
        except: pass
        
        prev_menu_id = menu_cache.get(tg_id)
        if prev_menu_id and not photo_url:
            try:
                await target.bot.edit_message_text(
                    chat_id=msg.chat.id, message_id=prev_menu_id,
                    text=text, parse_mode=parse_mode, reply_markup=reply_markup,
                )
                menu_cache.set(tg_id, prev_menu_id)
                try: await target.answer()
                except: pass
                return msg
            except TelegramBadRequest:
                try: await target.bot.delete_message(chat_id=msg.chat.id, message_id=prev_menu_id)
                except: pass
                menu_cache.delete(tg_id)
            except Exception:
                menu_cache.delete(tg_id)
        
        try: await target.answer()
        except: pass
        
        if photo_url:
            try:
                from aiogram.types import FSInputFile
                photo = photo_url if photo_url.startswith("http") else FSInputFile(photo_url)
                sent = await msg.answer_photo(photo, caption=text, parse_mode=parse_mode, reply_markup=reply_markup)
                menu_cache.set(tg_id, sent.message_id)
                return sent
            except: pass

        sent = await msg.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
        menu_cache.set(tg_id, sent.message_id)
        return sent

    # Для Message
    prev_id = menu_cache.get(tg_id)
    if prev_id and not photo_url:
        try:
            await target.bot.edit_message_text(
                chat_id=target.chat.id, message_id=prev_id,
                text=text, parse_mode=parse_mode, reply_markup=reply_markup,
            )
            menu_cache.set(tg_id, prev_id)
            return target
        except Exception:
            try: await target.bot.delete_message(chat_id=target.chat.id, message_id=prev_id)
            except: pass
            menu_cache.delete(tg_id)

    if photo_url:
        try:
            from aiogram.types import FSInputFile
            photo = photo_url if photo_url.startswith("http") else FSInputFile(photo_url)
            sent = await target.answer_photo(photo, caption=text, parse_mode=parse_mode, reply_markup=reply_markup)
            menu_cache.set(tg_id, sent.message_id)
            return sent
        except: pass

    sent = await target.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
    menu_cache.set(tg_id, sent.message_id)
    return sent