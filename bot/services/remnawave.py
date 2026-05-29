"""
Клиент Remnawave через официальный Python SDK.
pip install remnawave
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from remnawave import RemnawaveSDK
from remnawave.models import (
    UserResponseDto,
    CreateUserRequestDto,
    UpdateUserRequestDto,
    NodeResponseDto,
)

from config.settings import settings

# Единственный инстанс SDK на всё приложение
_sdk: Optional[RemnawaveSDK] = None


def get_sdk() -> RemnawaveSDK:
    global _sdk
    if _sdk is None:
        _sdk = RemnawaveSDK(
            base_url=settings.PANEL_API_URL,
            token=settings.PANEL_API_KEY,
        )
    return _sdk


# ── Users ──────────────────────────────────────────────────────────────────

async def username_exists(username: str) -> bool:
    """True если имя уже занято в панели."""
    try:
        user = await get_sdk().users.get_user_by_username(username)
        return user is not None
    except Exception:
        return False


async def get_user_by_uuid(uuid: str) -> Optional[UserResponseDto]:
    try:
        return await get_sdk().users.get_user_by_uuid(uuid)
    except Exception:
        return None


async def get_user_by_telegram_id(telegram_id: int) -> Optional[UserResponseDto]:
    """Ищем существующего пользователя по telegram_id — для миграции."""
    try:
        users = await get_sdk().users.get_users_by_telegram_id(telegram_id)
        if users and len(users) > 0:
            return users[0]
        return None
    except Exception:
        return None


async def create_user(
    username: str,
    duration_days: int,
    traffic_limit_gb: int = 0,
    device_limit: int = 0,
    telegram_id: Optional[int] = None,
) -> UserResponseDto:
    """Создаёт пользователя в Remnawave и возвращает объект."""
    expire_at = datetime.now(timezone.utc) + timedelta(days=duration_days)

    payload = CreateUserRequestDto(
        username=username,
        expire_at=expire_at,
        traffic_limit_bytes=traffic_limit_gb * 1024 ** 3 if traffic_limit_gb else 0,
        hwid_device_limit=device_limit,
        telegram_id=telegram_id,
    )
    return await get_sdk().users.create_user(payload)


async def extend_subscription(uuid: str, duration_days: int) -> UserResponseDto:
    """Продлевает подписку: берёт текущий expire_at и добавляет дни."""
    user = await get_user_by_uuid(uuid)
    if not user:
        raise Exception(f"User {uuid} not found in Remnawave")

    now = datetime.now(timezone.utc)
    # Если подписка ещё активна — продлеваем от текущего expire_at
    # Если уже истекла — считаем от сегодня
    base = user.expire_at if user.expire_at > now else now
    new_expire = base + timedelta(days=duration_days)

    payload = UpdateUserRequestDto(
        uuid=uuid,
        expire_at=new_expire,
        status="ACTIVE",
    )
    return await get_sdk().users.update_user(payload)


async def get_subscription_info(uuid: str) -> Optional[UserResponseDto]:
    """Данные подписки для личного кабинета."""
    return await get_user_by_uuid(uuid)


# ── Nodes ──────────────────────────────────────────────────────────────────

async def get_nodes() -> list[NodeResponseDto]:
    try:
        result = await get_sdk().nodes.get_all_nodes()
        return result if isinstance(result, list) else []
    except Exception:
        return []


async def restart_node(node_uuid: str) -> bool:
    try:
        await get_sdk().nodes.restart_node(node_uuid)
        return True
    except Exception:
        return False
