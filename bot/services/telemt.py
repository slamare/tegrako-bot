"""
Управление telemt MTProto прокси через HTTP API.

Telemt API поддерживает CRUD и персистит изменения в telemt.toml автоматически:
  POST   /v1/users          — создать пользователя
  PATCH  /v1/users/{name}   — обновить (max_unique_ips и др.)
  DELETE /v1/users/{name}   — удалить
  GET    /v1/users          — список всех
"""
import os
import secrets
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

TELEMT_API_URL = os.getenv("TELEMT_API_URL", "http://127.0.0.1:9091")
TIMEOUT = 10.0


def generate_secret() -> str:
    """Случайный 32-символьный hex-секрет."""
    return secrets.token_hex(16)


async def _get_user(username: str) -> Optional[dict]:
    """Получить данные пользователя из API. None если не найден."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{TELEMT_API_URL}/v1/users", timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            for user in data.get("data", []):
                if user.get("username") == username:
                    return user
    except Exception as e:
        logger.warning(f"telemt GET users failed: {e}")
    return None


async def add_user(username: str, secret: str, max_ips: int = 1) -> None:
    """
    Создаёт пользователя или обновляет max_ips если уже существует.
    Секрет сохраняется — при повторном создании с тем же секретом ссылка не меняется.
    """
    try:
        async with httpx.AsyncClient() as client:
            existing = await _get_user(username)
            if existing:
                resp = await client.patch(
                    f"{TELEMT_API_URL}/v1/users/{username}",
                    json={"max_unique_ips": max_ips},
                    timeout=TIMEOUT,
                )
                resp.raise_for_status()
                logger.info(f"telemt: updated {username} (max_ips={max_ips})")
            else:
                resp = await client.post(
                    f"{TELEMT_API_URL}/v1/users",
                    json={
                        "username": username,
                        "secret": secret,
                        "max_unique_ips": max_ips,
                    },
                    timeout=TIMEOUT,
                )
                resp.raise_for_status()
                logger.info(f"telemt: added {username} (max_ips={max_ips})")
    except Exception as e:
        logger.error(f"telemt add_user failed for {username}: {e}")
        raise


async def remove_user(username: str) -> None:
    """Удаляет пользователя из telemt (и из конфига)."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{TELEMT_API_URL}/v1/users/{username}",
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            logger.info(f"telemt: removed {username}")
    except Exception as e:
        logger.warning(f"telemt remove_user failed for {username}: {e}")


async def comment_user(username: str) -> None:
    """
    Отключает MTProto для пользователя.
    Telemt API не поддерживает комментирование, поэтому удаляем.
    Секрет остаётся в БД — при возобновлении подписки add_user воссоздаст с тем же секретом.
    """
    await remove_user(username)


async def user_exists(username: str) -> bool:
    """Проверяет существует ли пользователь в telemt."""
    user = await _get_user(username)
    return user is not None


async def get_proxy_link(username: str) -> Optional[str]:
    """Получает ссылку tg://proxy?... через API telemt."""
    user = await _get_user(username)
    if not user:
        return None
    links = user.get("links", {})
    tls_links = links.get("tls", [])
    if tls_links:
        return tls_links[0]
    return (
        links.get("secure", [None])[0]
        or links.get("classic", [None])[0]
    )


def build_link_fallback(secret: str) -> Optional[str]:
    """Запасной вариант: строим ссылку вручную из env-переменных."""
    host = os.getenv("TELEMT_PUBLIC_HOST", "")
    port = os.getenv("TELEMT_PUBLIC_PORT", "")
    if not host or not port:
        return None
    hex_host = host.encode().hex()
    full_secret = f"ee{secret}{hex_host}"
    return f"tg://proxy?server={host}&port={port}&secret={full_secret}"