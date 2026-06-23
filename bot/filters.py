from aiogram.filters import BaseFilter
from aiogram.types import Message, CallbackQuery
from config.settings import settings


class AdminFilter(BaseFilter):
    async def __call__(self, event) -> bool:
        if isinstance(event, (Message, CallbackQuery)):
            return event.from_user.id in settings.admin_ids
        return False
