"""Общие утилиты для хендлеров."""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramAPIError,
)

# ── Универсальное редактирование сообщений ─────────────────────────────────────

async def edit_or_answer(
    callback: CallbackQuery,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
):
    """
    Универсальное редактирование: edit_caption для фото, edit_text для текста.
    Если редактирование не удалось — удаляет старое сообщение и отправляет новое.
    """
    msg = callback.message
    edited = False
    
    try:
        if msg.photo:
            await msg.edit_caption(
                caption=text, parse_mode=parse_mode, reply_markup=reply_markup
            )
        else:
            await msg.edit_text(
                text, parse_mode=parse_mode, reply_markup=reply_markup
            )
        edited = True
    except TelegramBadRequest as e:
        error_msg = str(e).lower()
        
        if "message is not modified" in error_msg:
            edited = True
        elif any(x in error_msg for x in [
            "message to edit not found",
            "message can't be edited",
            "message is not modified",
            "there is no text",
        ]):
            try:
                await msg.delete()
            except Exception:
                pass
            await msg.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
            edited = True
        else:
            try:
                await msg.delete()
            except Exception:
                pass
            await msg.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
            edited = True
    except TelegramAPIError:
        try:
            await msg.delete()
        except Exception:
            pass
        await msg.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
        edited = True
    except Exception:
        try:
            await msg.delete()
        except Exception:
            pass
        await msg.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
        edited = True
    
    try:
        await callback.answer()
    except Exception:
        pass
    
    return edited

# ── Очистка предыдущего сообщения из FSM ──────────────────────────────────────

async def clear_previous_warning(state: FSMContext):
    """Удаляет предыдущее предупреждающее сообщение из FSM."""
    data = await state.get_data()
    warning_msg_id = data.get("warning_message_id")
    chat_id = data.get("warning_chat_id")
    
    if warning_msg_id and chat_id:
        try:
            from aiogram import Bot
        except Exception:
            pass
    
    await state.update_data(warning_message_id=None, warning_chat_id=None)

# ── Middleware для автоудаления FSM-сообщений ──────────────────────────────────

class FSMMessageCleanupMiddleware(BaseMiddleware):
    """Автоматически удаляет сообщения пользователя через N секунд после FSM-обработки."""

    def __init__(self, delay: int = 30):
        self.delay = delay

    async def __call__(self, handler, event, data):
        state = data.get("state")
        should_cleanup = False

        if state and isinstance(event, Message):
            current_state = await state.get_state()
            if current_state is not None:
                should_cleanup = True
                await clear_previous_warning(state)

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
    """
    Показывает главное меню, ВСЕГДА удаляя текущее сообщение callback'а.
    Затем либо редактирует кэшированное меню, либо отправляет новое.
    """
    tg_id = target.from_user.id
    
    if isinstance(target, CallbackQuery):
        msg = target.message
        
        # ВСЕГДА удаляем текущее сообщение callback'а
        try:
            await msg.delete()
        except Exception:
            pass
        
        # Пытаемся отредактировать кэшированное меню (только если нет photo_url)
        prev_menu_id = menu_cache.get(tg_id)
        if prev_menu_id and not photo_url:
            try:
                await target.bot.edit_message_text(
                    chat_id=msg.chat.id,
                    message_id=prev_menu_id,
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
                menu_cache.set(tg_id, prev_menu_id)
                
                try:
                    await target.answer()
                except Exception:
                    pass
                
                return msg
            except TelegramBadRequest as e:
                error_msg = str(e).lower()
                
                if "message is not modified" in error_msg:
                    menu_cache.set(tg_id, prev_menu_id)
                    try:
                        await target.answer()
                    except Exception:
                        pass
                    return msg
                
                if any(x in error_msg for x in [
                    "message to edit not found",
                    "message can't be edited",
                ]):
                    # Удаляем старое кэшированное сообщение
                    try:
                        await target.bot.delete_message(
                            chat_id=msg.chat.id, message_id=prev_menu_id
                        )
                    except Exception:
                        pass
                    menu_cache.delete(tg_id)
            except TelegramAPIError:
                try:
                    await target.bot.delete_message(
                        chat_id=msg.chat.id, message_id=prev_menu_id
                    )
                except Exception:
                    pass
                menu_cache.delete(tg_id)
            except Exception:
                try:
                    await target.bot.delete_message(
                        chat_id=msg.chat.id, message_id=prev_menu_id
                    )
                except Exception:
                    pass
                menu_cache.delete(tg_id)
        
        # Отправляем новое сообщение
        try:
            await target.answer()
        except Exception:
            pass
        
        if photo_url:
            try:
                from aiogram.types import FSInputFile
                photo = (
                    photo_url if photo_url.startswith("http")
                    else FSInputFile(photo_url)
                )
                sent = await msg.answer_photo(
                    photo, caption=text, parse_mode=parse_mode, reply_markup=reply_markup
                )
                menu_cache.set(tg_id, sent.message_id)
                return sent
            except Exception:
                pass

        sent = await msg.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
        menu_cache.set(tg_id, sent.message_id)
        return sent

    # Для Message (не callback)
    prev_id = menu_cache.get(tg_id)
    if prev_id and not photo_url:
        try:
            await target.bot.edit_message_text(
                chat_id=target.chat.id,
                message_id=prev_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            menu_cache.set(tg_id, prev_id)
            return target
        except TelegramBadRequest as e:
            error_msg = str(e).lower()
            
            if "message is not modified" in error_msg:
                menu_cache.set(tg_id, prev_id)
                return target
            
            if any(x in error_msg for x in [
                "message to edit not found",
                "message can't be edited",
            ]):
                try:
                    await target.bot.delete_message(
                        chat_id=target.chat.id, message_id=prev_id
                    )
                except Exception:
                    pass
                menu_cache.delete(tg_id)
            else:
                try:
                    await target.bot.delete_message(
                        chat_id=target.chat.id, message_id=prev_id
                    )
                except Exception:
                    pass
                menu_cache.delete(tg_id)
        except TelegramAPIError:
            try:
                await target.bot.delete_message(
                    chat_id=target.chat.id, message_id=prev_id
                )
            except Exception:
                pass
            menu_cache.delete(tg_id)
        except Exception:
            try:
                await target.bot.delete_message(
                    chat_id=target.chat.id, message_id=prev_id
                )
            except Exception:
                pass
            menu_cache.delete(tg_id)

    # Отправляем новое сообщение
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