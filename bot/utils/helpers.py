"""Общие утилиты для хендлеров."""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError


# ── Удаление сообщений ────────────────────────────────────────────────────

async def delete_later(bot, chat_id: int, message_id: int, delay: int = 30) -> None:
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def cleanup_fsm_interaction(
    message: Message,
    state: FSMContext,
    final_msg: Optional[Message] = None,
    final_delay: int = 30,
) -> None:
    """Удаляет сообщение пользователя и предыдущий промпт бота из state."""
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
        asyncio.create_task(
            delete_later(message.bot, message.chat.id, final_msg.message_id, final_delay)
        )


# ── Редактирование сообщений ──────────────────────────────────────────────

_EDIT_NOT_FOUND_ERRORS = (
    "message to edit not found",
    "message can't be edited",
    "there is no text",
)


async def _safe_edit(msg: Message, text: str, reply_markup, parse_mode: str) -> bool:
    """Пробует отредактировать сообщение. Возвращает True при успехе."""
    try:
        if msg.photo:
            await msg.edit_caption(caption=text, parse_mode=parse_mode, reply_markup=reply_markup)
        else:
            await msg.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        return True
    except TelegramBadRequest as e:
        err = str(e).lower()
        return "message is not modified" in err
    except (TelegramAPIError, Exception):
        return False


async def edit_or_answer(
    callback: CallbackQuery,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
) -> Optional[Message]:
    """
    Редактирует текущее сообщение callback'а.
    Если редактирование невозможно — удаляет старое и отправляет новое.
    Возвращает новое Message только если отправлялось новое, иначе None.
    """
    msg = callback.message
    sent: Optional[Message] = None

    edited = await _safe_edit(msg, text, reply_markup, parse_mode)
    if not edited:
        try:
            await msg.delete()
        except Exception:
            pass
        sent = await msg.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)

    try:
        await callback.answer()
    except Exception:
        pass

    return sent


# ── Кэш сообщений меню ────────────────────────────────────────────────────

class _MenuMessageCache:
    """Хранит message_id главного меню для каждого пользователя (TTL 10 мин)."""

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

    def set(self, tg_id: int, message_id: int) -> None:
        self._data[tg_id] = (message_id, time.time())

    def delete(self, tg_id: int) -> None:
        self._data.pop(tg_id, None)


menu_cache = _MenuMessageCache(ttl=600)


async def _try_edit_cached_menu(
    bot,
    chat_id: int,
    message_id: int,
    tg_id: int,
    text: str,
    reply_markup,
    parse_mode: str,
) -> bool:
    """Пробует отредактировать кэшированное меню. При неудаче — удаляет и чистит кэш."""
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        menu_cache.set(tg_id, message_id)
        return True
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            menu_cache.set(tg_id, message_id)
            return True
        # Сообщение удалено или недоступно — чистим кэш
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass
        menu_cache.delete(tg_id)
        return False
    except (TelegramAPIError, Exception):
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass
        menu_cache.delete(tg_id)
        return False


async def _send_menu_message(
    target,
    chat_id: int,
    tg_id: int,
    text: str,
    reply_markup,
    parse_mode: str,
    photo_url: Optional[str],
) -> Message:
    """Отправляет новое сообщение меню и кэширует его."""
    if photo_url:
        try:
            from aiogram.types import FSInputFile
            photo = photo_url if photo_url.startswith("http") else FSInputFile(photo_url)
            # answer работает как для Message, так и для callback.message
            answer = getattr(target, "answer", None) or target.message.answer
            sent = await answer(photo, caption=text, parse_mode=parse_mode, reply_markup=reply_markup)
            # answer_photo у Message принимает photo как первый аргумент
        except Exception:
            pass

    answer_fn = getattr(target, "answer", None)
    if answer_fn is None:
        answer_fn = target.message.answer

    sent = await answer_fn(text, parse_mode=parse_mode, reply_markup=reply_markup)
    menu_cache.set(tg_id, sent.message_id)
    return sent


async def show_menu_message(
    target,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
    photo_url: Optional[str] = None,
) -> Message:
    """
    Показывает главное меню. Всегда удаляет текущее сообщение callback'а,
    затем редактирует кэшированное меню или отправляет новое.
    """
    tg_id = target.from_user.id

    if isinstance(target, CallbackQuery):
        msg = target.message
        try:
            await msg.delete()
        except Exception:
            pass

        prev_id = menu_cache.get(tg_id)
        if prev_id and not photo_url:
            ok = await _try_edit_cached_menu(
                target.bot, msg.chat.id, prev_id, tg_id, text, reply_markup, parse_mode
            )
            if ok:
                try:
                    await target.answer()
                except Exception:
                    pass
                return msg

        try:
            await target.answer()
        except Exception:
            pass

        if photo_url:
            try:
                from aiogram.types import FSInputFile
                photo = photo_url if photo_url.startswith("http") else FSInputFile(photo_url)
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

    # Message path
    prev_id = menu_cache.get(tg_id)
    if prev_id and not photo_url:
        ok = await _try_edit_cached_menu(
            target.bot, target.chat.id, prev_id, tg_id, text, reply_markup, parse_mode
        )
        if ok:
            return target

    if photo_url:
        try:
            from aiogram.types import FSInputFile
            photo = photo_url if photo_url.startswith("http") else FSInputFile(photo_url)
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
