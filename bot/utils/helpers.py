"""Общие утилиты для хендлеров."""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message


# ── Универсальное редактирование сообщений ─────────────────────────────────────

async def edit_or_answer(
    callback: CallbackQuery,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
):
    """Универсальное редактирование: edit_caption для фото, edit_text для текста."""
    msg = callback.message
    try:
        if msg.photo:
            await msg.edit_caption(
                caption=text, parse_mode=parse_mode, reply_markup=reply_markup
            )
        else:
            await msg.edit_text(
                text, parse_mode=parse_mode, reply_markup=reply_markup
            )
    except Exception:
        await msg.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
    await callback.answer()


# ── Middleware для автоудаления FSM-сообщений ──────────────────────────────────

class FSMMessageCleanupMiddleware(BaseMiddleware):
    """Автоматически удаляет сообщения пользователя через N секунд после FSM-обработки."""
    
    def __init__(self, delay: int = 30):
        self.delay = delay
    
    async def __call__(self, handler, event, data):
        from aiogram.fsm.context import FSMContext
        
        state = data.get("state")
        should_cleanup = False
        
        if state and isinstance(event, Message):
            current_state = await state.get_state()
            if current_state is not None:
                should_cleanup = True
        
        result = await handler(event, data)
        
        if should_cleanup:
            asyncio.create_task(self._delete_later(event, self.delay))
        
        return result
    
    async def _delete_later(self, message: Message, delay: int):
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except Exception:
            pass


# ── Кэш последних сообщений меню ─────────────────────────────────────────────

class _MenuMessageCache:
    """Хранит message_id главного меню для каждого пользователя."""

    def __init__(self, ttl: int = 600):
        self._ttl = ttl
        self._data: dict[int, tuple[int, float]] = {}

    def get(self, tg_id: int) -> Optional[int]:
        entry = self._data.get(tg_id)
        if not entry:
            return None
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
    """Показывает главное меню, редактируя предыдущее сообщение если возможно."""
    tg_id = target.from_user.id

    if isinstance(target, CallbackQuery):
        msg = target.message
        try:
            if msg.photo:
                await msg.edit_caption(
                    caption=text, parse_mode=parse_mode, reply_markup=reply_markup
                )
            else:
                await msg.edit_text(
                    text, parse_mode=parse_mode, reply_markup=reply_markup
                )
        except Exception:
            msg = await msg.answer(
                text, parse_mode=parse_mode, reply_markup=reply_markup
            )
        await target.answer()
        menu_cache.set(tg_id, msg.message_id)
        return msg

    prev_id = menu_cache.get(tg_id)
    if prev_id:
        try:
            if photo_url:
                raise ValueError("Cannot add photo to existing text message")
            await target.bot.edit_message_text(
                chat_id=target.chat.id,
                message_id=prev_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            menu_cache.set(tg_id, prev_id)
            return target
        except Exception:
            try:
                await target.bot.delete_message(
                    chat_id=target.chat.id, message_id=prev_id
                )
            except Exception:
                pass
            menu_cache.delete(tg_id)

    if photo_url:
        try:
            from aiogram.types import FSInputFile
            photo = (
                photo_url if photo_url.startswith("http")
                else FSInputFile(photo_url)
            )
            sent = await target.answer_photo(
                photo, caption=text, parse_mode=parse_mode, reply_markup=reply_markup
            )
            menu_cache.set(tg_id, sent.message_id)
            return sent
        except Exception:
            pass

    sent = await target.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
    menu_cache.set(tg_id, sent.message_id)
    return sent