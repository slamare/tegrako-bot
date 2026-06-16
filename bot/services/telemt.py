"""
Управление telemt MTProto прокси.

Telemt поддерживает hot-reload конфига — изменения в telemt.toml
подхватываются без перезапуска. Ссылки получаем через REST API на порту 9091.
"""
import os
import re
import secrets
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

TELEMT_CONFIG_PATH = os.getenv("TELEMT_CONFIG_PATH", "/opt/telemt/config/telemt.toml")
TELEMT_API_URL = os.getenv("TELEMT_API_URL", "http://127.0.0.1:9091")


# ── Config helpers ─────────────────────────────────────────────────────────

def _read_config() -> str:
    with open(TELEMT_CONFIG_PATH, "r") as f:
        return f.read()


def _write_config(content: str) -> None:
    tmp = TELEMT_CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.replace(tmp, TELEMT_CONFIG_PATH)


def _add_user_line(config: str, username: str, secret: str) -> str:
    """Вставляет строку пользователя последней в секцию [access.users]."""
    lines = config.splitlines()
    new_line = f'{username} = "{secret}"'

    # Ищем секцию [access.users]
    section_start = None
    for i, line in enumerate(lines):
        if line.strip() == "[access.users]":
            section_start = i
            break

    if section_start is None:
        return config.rstrip("\n") + "\n[access.users]\n" + new_line + "\n"

    # Ищем конец секции (следующая несвязанная секция)
    section_end = len(lines)
    for i in range(section_start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("[") and not stripped.startswith("[["):
            section_name = stripped.lstrip("[").rstrip("]").strip()
            if not section_name.startswith("access."):
                section_end = i
                break

    # Вставляем после последней непустой строки с данными в секции
    insert_at = section_end
    for i in range(section_end - 1, section_start, -1):
        if lines[i].strip() and not lines[i].strip().startswith("#"):
            insert_at = i + 1
            break

    lines.insert(insert_at, new_line)
    return "\n".join(lines) + "\n"


def _remove_user_line(config: str, username: str) -> str:
    """Удаляет строку пользователя из конфига."""
    result = []
    for line in config.splitlines():
        m = re.match(r'^(\w+)\s*=\s*"[0-9a-f]{32}"', line.strip())
        if m and m.group(1) == username:
            continue
        result.append(line)
    return "\n".join(result) + "\n"


def _user_in_config(config: str, username: str) -> bool:
    for line in config.splitlines():
        m = re.match(r'^(\w+)\s*=\s*"[0-9a-f]{32}"', line.strip())
        if m and m.group(1) == username:
            return True
    return False


# ── Public API ─────────────────────────────────────────────────────────────

def generate_secret() -> str:
    """Случайный 32-символьный hex-секрет."""
    return secrets.token_hex(16)


def _add_ip_limit_line(config: str, username: str, max_ips: int) -> str:
    """Вставляет/обновляет строку в [access.user_max_unique_ips]."""
    lines = config.splitlines()
    new_line = f"{username} = {max_ips}"

    section_start = None
    for i, line in enumerate(lines):
        if line.strip() == "[access.user_max_unique_ips]":
            section_start = i
            break

    if section_start is None:
        return config.rstrip("\n") + "\n[access.user_max_unique_ips]\n" + new_line + "\n"

    # Если пользователь уже есть в секции — обновляем
    for i in range(section_start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("["):
            break
        m = __import__("re").match(r'^(\w+)\s*=\s*(\d+)', stripped)
        if m and m.group(1) == username:
            lines[i] = new_line
            return "\n".join(lines) + "\n"

    # Новый — ищем конец секции и вставляем
    section_end = len(lines)
    for i in range(section_start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("[") and not stripped.startswith("[["):
            section_name = stripped.lstrip("[").rstrip("]").strip()
            if not section_name.startswith("access."):
                section_end = i
                break

    insert_at = section_end
    for i in range(section_end - 1, section_start, -1):
        if lines[i].strip() and not lines[i].strip().startswith("#"):
            insert_at = i + 1
            break

    lines.insert(insert_at, new_line)
    return "\n".join(lines) + "\n"


def _remove_ip_limit_line(config: str, username: str) -> str:
    """Удаляет строку пользователя из [access.user_max_unique_ips]."""
    import re
    result = []
    for line in config.splitlines():
        m = re.match(r'^(\w+)\s*=\s*(\d+)', line.strip())
        if m and m.group(1) == username:
            continue
        result.append(line)
    return "\n".join(result) + "\n"


def add_user(username: str, secret: str, max_ips: int = 1) -> None:
    """Добавляет пользователя в конфиг. Hot-reload, рестарт не нужен."""
    config = _read_config()
    if not _user_in_config(config, username):
        config = _add_user_line(config, username, secret)
    config = _add_ip_limit_line(config, username, max_ips)
    _write_config(config)
    logger.info(f"telemt: added user {username} (max_ips={max_ips})")


def remove_user(username: str) -> None:
    """Удаляет пользователя из конфига."""
    config = _read_config()
    if not _user_in_config(config, username):
        return
    config = _remove_user_line(config, username)
    config = _remove_ip_limit_line(config, username)
    _write_config(config)
    logger.info(f"telemt: removed user {username}")


def user_exists(username: str) -> bool:
    try:
        return _user_in_config(_read_config(), username)
    except Exception:
        return False


async def get_proxy_link(username: str) -> Optional[str]:
    """
    Получает ссылку tg://proxy?... через API telemt.
    Возвращает None если API недоступен.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{TELEMT_API_URL}/v1/users", timeout=5)
            data = resp.json()
            for user in data.get("users", []):
                if user.get("username") == username:
                    links = user.get("links", {})
                    # FakeTLS (ee) предпочтительнее всего
                    return (
                        links.get("ee_tls")
                        or links.get("secure")
                        or links.get("classic")
                    )
    except Exception as e:
        logger.warning(f"telemt API error for {username}: {e}")
    return None


def build_link_fallback(secret: str) -> Optional[str]:
    """
    Запасной вариант: строим ссылку вручную из env-переменных
    TELEMT_PUBLIC_HOST и TELEMT_PUBLIC_PORT если API недоступен.
    """
    host = os.getenv("TELEMT_PUBLIC_HOST", "")
    port = os.getenv("TELEMT_PUBLIC_PORT", "")
    if not host or not port:
        return None
    hex_host = host.encode().hex()
    full_secret = f"ee{secret}{hex_host}"
    return f"tg://proxy?server={host}&port={port}&secret={full_secret}"
