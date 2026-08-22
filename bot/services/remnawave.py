"""
Клиент Remnawave через httpx.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from dataclasses import dataclass

import httpx
from cachetools import TTLCache

from config.settings import settings

logger = logging.getLogger(__name__)


# ── Shared client ──────────────────────────────────────────────────────────

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(verify=True, timeout=10)
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.PANEL_API_KEY}"}


def _url(path: str) -> str:
    return f"{settings.PANEL_API_URL}/api{path}"


# ── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class UserTraffic:
    used_traffic_bytes: int


@dataclass
class UserStatus:
    value: str


@dataclass
class UserInfo:
    uuid: str
    username: str
    status: UserStatus
    expire_at: datetime
    traffic_limit_bytes: int
    user_traffic: UserTraffic
    subscription_url: str
    hwid_device_limit: int
    telegram_id: Optional[int]


@dataclass
class NodeInfo:
    uuid: str
    name: str
    address: str
    is_connected: bool


@dataclass
class HwidDevice:
    hwid: str
    user_uuid: str
    platform: Optional[str]
    os_version: Optional[str]
    device_model: Optional[str]
    user_agent: Optional[str]
    created_at: str


@dataclass
class SquadInfo:
    uuid: str
    name: str
    members_count: int


@dataclass
class InboundInfo:
    uuid: str
    tag: str
    type: str
    is_enabled: bool


@dataclass
class HostInfo:
    uuid: str
    remark: str
    address: str
    port: int
    inbound_uuid: str
    is_enabled: bool


def _parse_user(u: dict) -> UserInfo:
    expire_at = datetime.fromisoformat(u["expireAt"].replace("Z", "+00:00"))
    traffic = u.get("userTraffic") or {}
    return UserInfo(
        uuid=str(u["id"]),
        username=u["username"],
        status=UserStatus(value=u.get("status", "UNKNOWN")),
        expire_at=expire_at,
        traffic_limit_bytes=u.get("trafficLimitBytes", 0),
        user_traffic=UserTraffic(used_traffic_bytes=traffic.get("usedTrafficBytes", 0)),
        subscription_url=u.get("subscriptionUrl", ""),
        hwid_device_limit=u.get("hwidDeviceLimit", 0),
        telegram_id=u.get("telegramId"),
    )


# ── Кэш get_subscription_info ──────────────────────────────────────────────

_sub_info_cache: TTLCache = TTLCache(maxsize=500, ttl=45)
# Словарь блокировок очищается вместе с кэшем через weakref-подобную логику:
# ключи удаляются из _sub_info_locks только при invalidate, чтобы не рос без предела.
_sub_info_locks: dict[str, asyncio.Lock] = {}


def invalidate_sub_info_cache(uuid: Optional[str] = None) -> None:
    if uuid is None:
        _sub_info_cache.clear()
        _sub_info_locks.clear()
    else:
        _sub_info_cache.pop(uuid, None)
        _sub_info_locks.pop(uuid, None)


# ── Users ──────────────────────────────────────────────────────────────────

async def username_exists(username: str) -> bool:
    try:
        resp = await _get_client().get(
            _url(f"/users/by-username/{username}"), headers=_headers()
        )
        return resp.status_code == 200
    except Exception as e:
        logger.warning(f"username_exists({username}) failed: {e!r}")
        return False


async def get_user_by_uuid(uuid: str) -> Optional[UserInfo]:
    try:
        resp = await _get_client().get(_url(f"/users/{uuid}"), headers=_headers())
        if resp.status_code != 200:
            logger.warning(f"get_user_by_uuid({uuid}) got status {resp.status_code}: {resp.text[:200]}")
            return None
        data = resp.json()
        return _parse_user(data.get("response", data))
    except Exception as e:
        logger.warning(f"get_user_by_uuid({uuid}) failed: {e!r}")
        return None


async def get_user_by_telegram_id(telegram_id: int) -> Optional[UserInfo]:
    try:
        resp = await _get_client().get(
            _url("/users/stream"), headers=_headers(),
            params={"telegramId": telegram_id, "size": 5}, timeout=15,
        )
        users = resp.json().get("response", {}).get("users", [])
        return _parse_user(users[0]) if users else None
    except Exception as e:
        logger.warning(f"get_user_by_telegram_id({telegram_id}) failed: {e!r}")
        return None


async def get_all_users_bulk() -> list[UserInfo]:
    """Все пользователи через users/stream с курсорной пагинацией. Используется в scheduler."""
    try:
        result = []
        cursor = None
        while True:
            params = {"size": 1000}
            if cursor is not None:
                params["cursor"] = cursor
            resp = await _get_client().get(
                _url("/users/stream"), headers=_headers(), params=params, timeout=30
            )
            data = resp.json().get("response", {})
            users = data.get("users", [])
            result.extend(_parse_user(u) for u in users)
            cursor = data.get("nextCursor")
            if not cursor or not users:
                break
        return result
    except Exception as e:
        logger.warning(f"get_all_users_bulk() failed: {e!r}")
        return []


async def create_user(
    username: str,
    duration_days: int,
    traffic_limit_gb: int = 0,
    device_limit: int = 0,
    telegram_id: Optional[int] = None,
) -> UserInfo:
    expire_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
    payload = {
        "username": username,
        "expireAt": expire_at.isoformat(),
        "trafficLimitBytes": traffic_limit_gb * 1024 ** 3 if traffic_limit_gb else 0,
        "hwidDeviceLimit": device_limit,
        "telegramId": telegram_id,
        "status": "ACTIVE",
    }
    resp = await _get_client().post(_url("/users"), headers=_headers(), json=payload)
    resp.raise_for_status()
    return _parse_user(resp.json().get("response", resp.json()))


async def _patch_user(payload: dict) -> Optional[UserInfo]:
    """Общий PATCH /api/users — используется всеми функциями изменения пользователя."""
    user_id = payload.pop("uuid", None)
    if user_id is not None:
        payload["id"] = int(user_id)
    try:
        resp = await _get_client().patch(_url("/users"), headers=_headers(), json=payload)
        resp.raise_for_status()
        result = _parse_user(resp.json().get("response", resp.json()))
        invalidate_sub_info_cache(str(payload.get("id")))
        return result
    except Exception as e:
        logger.warning(f"_patch_user id={payload.get('id')} fields={list(payload.keys())} failed: {e!r}")
        return None


async def extend_subscription(uuid: str, duration_days: int) -> UserInfo:
    try:
        resp = await _get_client().post(
            _url(f"/users/{uuid}/actions/extend"), headers=_headers(), json={"days": duration_days}
        )
        resp.raise_for_status()
        result = _parse_user(resp.json().get("response", resp.json()))
        invalidate_sub_info_cache(uuid)
        return result
    except Exception as e:
        logger.warning(f"extend_subscription({uuid}) failed: {e!r}")
        raise RuntimeError(f"Failed to extend subscription for {uuid}") from e


async def set_expire_at(uuid: str, expire_at: datetime) -> Optional[UserInfo]:
    return await _patch_user({
        "uuid": uuid,
        "expireAt": expire_at.isoformat(),
        "status": "ACTIVE",
    })


async def update_user_limits(
    uuid: str,
    traffic_limit_gb: Optional[int] = None,
    device_limit: Optional[int] = None,
) -> Optional[UserInfo]:
    payload: dict = {"uuid": uuid}
    if traffic_limit_gb is not None:
        payload["trafficLimitBytes"] = traffic_limit_gb * 1024 ** 3
    if device_limit is not None:
        payload["hwidDeviceLimit"] = device_limit
    return await _patch_user(payload)


async def set_user_status(uuid: str, status: str) -> Optional[UserInfo]:
    return await _patch_user({"uuid": uuid, "status": status})


async def delete_panel_user(uuid: str) -> bool:
    try:
        resp = await _get_client().delete(_url(f"/users/{uuid}"), headers=_headers())
        ok = resp.status_code in (200, 204)
        if ok:
            invalidate_sub_info_cache(uuid)
        else:
            logger.warning(f"delete_panel_user({uuid}) got status {resp.status_code}: {resp.text[:200]}")
        return ok
    except Exception as e:
        logger.warning(f"delete_panel_user({uuid}) failed: {e!r}")
        return False


async def reset_user_traffic(uuid: str) -> bool:
    try:
        resp = await _get_client().post(
            _url(f"/users/{uuid}/actions/reset-traffic"), headers=_headers()
        )
        ok = resp.status_code in (200, 201, 204)
        if ok:
            invalidate_sub_info_cache(uuid)
        else:
            logger.warning(f"reset_user_traffic({uuid}) got status {resp.status_code}: {resp.text[:200]}")
        return ok
    except Exception as e:
        logger.warning(f"reset_user_traffic({uuid}) failed: {e!r}")
        return False


async def get_subscription_info(uuid: str) -> Optional[UserInfo]:
    """Получить подписку с кэшем (TTL 45 сек). Double-checked locking."""
    cached = _sub_info_cache.get(uuid)
    if cached is not None:
        return cached

    lock = _sub_info_locks.setdefault(uuid, asyncio.Lock())
    async with lock:
        cached = _sub_info_cache.get(uuid)
        if cached is not None:
            return cached
        result = await get_user_by_uuid(uuid)
        if result is not None:
            _sub_info_cache[uuid] = result
        return result


async def revoke_subscription(uuid: str) -> Optional[UserInfo]:
    try:
        resp = await _get_client().post(
            _url(f"/users/{uuid}/actions/revoke"), headers=_headers()
        )
        if resp.status_code != 200:
            logger.warning(f"revoke_subscription({uuid}) got status {resp.status_code}: {resp.text[:200]}")
            return None
        result = _parse_user(resp.json().get("response", resp.json()))
        invalidate_sub_info_cache(uuid)
        return result
    except Exception as e:
        logger.warning(f"revoke_subscription({uuid}) failed: {e!r}")
        return None


# ── Squads ─────────────────────────────────────────────────────────────────

async def get_internal_squads() -> list[SquadInfo]:
    try:
        resp = await _get_client().get(_url("/internal-squads"), headers=_headers())
        squads = resp.json().get("response", {}).get("internalSquads", [])
        return [
            SquadInfo(
                uuid=s["uuid"],
                name=s["name"],
                members_count=s.get("info", {}).get("membersCount", 0),
            )
            for s in squads
        ]
    except Exception as e:
        logger.warning(f"get_internal_squads() failed: {e!r}")
        return []


async def add_user_to_squad(user_uuid: str, squad_uuid: str) -> bool:
    try:
        resp = await _get_client().post(
            _url(f"/internal-squads/{squad_uuid}/bulk-actions/add-many-users"),
            headers=_headers(),
            json={"userIds": [int(user_uuid)]},
        )
        return resp.status_code in (200, 202, 204)
    except Exception as e:
        logger.warning(f"add_user_to_squad({user_uuid}, {squad_uuid}) failed: {e!r}")
        return False


async def add_user_to_default_squad(
    user_uuid: str, tariff_squad_uuid: Optional[str] = None
) -> bool:
    squad_uuid = tariff_squad_uuid or settings.DEFAULT_SQUAD_UUID
    if not squad_uuid:
        return False
    return await add_user_to_squad(user_uuid, squad_uuid)


# ── HWID Devices ──────────────────────────────────────────────────────────

async def get_user_devices(user_uuid: str) -> list[HwidDevice]:
    try:
        resp = await _get_client().get(
            _url(f"/hwid/devices/{user_uuid}"), headers=_headers()
        )
        devices = resp.json().get("response", {}).get("devices", [])
        return [
            HwidDevice(
                hwid=d["hwid"],
                user_uuid=str(d.get("userId", d.get("userUuid", user_uuid))),
                platform=d.get("platform"),
                os_version=d.get("osVersion"),
                device_model=d.get("deviceModel"),
                user_agent=d.get("userAgent"),
                created_at=d.get("createdAt", ""),
            )
            for d in devices
        ]
    except Exception as e:
        logger.warning(f"get_user_devices({user_uuid}) failed: {e!r}")
        return []


async def delete_user_device(user_uuid: str, hwid: str) -> bool:
    try:
        resp = await _get_client().post(
            _url("/hwid/devices/delete"),
            headers=_headers(),
            json={"userId": int(user_uuid), "hwid": hwid},
        )
        return resp.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"delete_user_device({user_uuid}, {hwid}) failed: {e!r}")
        return False


async def delete_all_user_devices(user_uuid: str) -> bool:
    try:
        resp = await _get_client().post(
            _url("/hwid/devices/delete-all"),
            headers=_headers(),
            json={"userId": int(user_uuid)},
        )
        return resp.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"delete_all_user_devices({user_uuid}) failed: {e!r}")
        return False


# ── Nodes ──────────────────────────────────────────────────────────────────

async def get_nodes() -> list[NodeInfo]:
    try:
        resp = await _get_client().get(_url("/nodes"), headers=_headers())
        return [
            NodeInfo(
                uuid=n["uuid"],
                name=n["name"],
                address=n["address"],
                is_connected=n.get("isConnected", False),
            )
            for n in resp.json().get("response", [])
        ]
    except Exception as e:
        logger.warning(f"get_nodes() failed: {e!r}")
        return []


async def restart_node(node_uuid: str) -> bool:
    try:
        resp = await _get_client().post(
            _url(f"/nodes/{node_uuid}/restart"), headers=_headers()
        )
        return resp.status_code in (200, 201)
    except Exception as e:
        logger.warning(f"restart_node({node_uuid}) failed: {e!r}")
        return False


# ── Inbounds & Hosts ───────────────────────────────────────────────────────

async def get_inbounds() -> list[InboundInfo]:
    try:
        resp = await _get_client().get(
            _url("/config-profiles/inbounds"), headers=_headers()
        )
        if resp.status_code != 200:
            logger.warning(f"get_inbounds() got status {resp.status_code}: {resp.text[:200]}")
            return []
        raw = resp.json().get("response", {})
        items = raw.get("inbounds", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
        return [
            InboundInfo(
                uuid=i["uuid"],
                tag=i.get("tag", ""),
                type=i.get("type", ""),
                is_enabled=i.get("isEnabled", True),
            )
            for i in items
        ]
    except Exception as e:
        logger.warning(f"get_inbounds() failed: {e!r}")
        return []


async def get_hosts() -> list[HostInfo]:
    try:
        resp = await _get_client().get(_url("/hosts"), headers=_headers())
        if resp.status_code != 200:
            logger.warning(f"get_hosts() got status {resp.status_code}: {resp.text[:200]}")
            return []
        raw = resp.json().get("response", [])
        if not isinstance(raw, list):
            return []
        return [
            HostInfo(
                uuid=h["uuid"],
                remark=h.get("remark", ""),
                address=h.get("address", ""),
                port=h.get("port", 0),
                inbound_uuid=h.get("inboundUuid", ""),
                is_enabled=h.get("isEnabled", True),
            )
            for h in raw
        ]
    except Exception as e:
        logger.warning(f"get_hosts() failed: {e!r}")
        return []
