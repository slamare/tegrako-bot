"""
Клиент Remnawave через httpx напрямую.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
from dataclasses import dataclass

import httpx
from config.settings import settings


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
        uuid=u["uuid"],
        username=u["username"],
        status=UserStatus(value=u.get("status", "UNKNOWN")),
        expire_at=expire_at,
        traffic_limit_bytes=u.get("trafficLimitBytes", 0),
        user_traffic=UserTraffic(used_traffic_bytes=traffic.get("usedTrafficBytes", 0)),
        subscription_url=u.get("subscriptionUrl", ""),
        hwid_device_limit=u.get("hwidDeviceLimit", 0),
        telegram_id=u.get("telegramId"),
    )


# ── Users ──────────────────────────────────────────────────────────────────

async def username_exists(username: str) -> bool:
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.get(
                _url(f"/users/by-username/{username}"), headers=_headers(), timeout=10
            )
            return resp.status_code == 200
    except Exception:
        return False


async def get_user_by_uuid(uuid: str) -> Optional[UserInfo]:
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.get(_url(f"/users/{uuid}"), headers=_headers(), timeout=10)
            if resp.status_code != 200:
                return None
            data = resp.json()
            u = data.get("response", data)
            return _parse_user(u)
    except Exception:
        return None


async def get_user_by_telegram_id(telegram_id: int) -> Optional[UserInfo]:
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.get(_url("/users?limit=1000"), headers=_headers(), timeout=15)
            data = resp.json()
            users = data.get("response", {}).get("users", [])
            u = next((x for x in users if x.get("telegramId") == telegram_id), None)
            return _parse_user(u) if u else None
    except Exception:
        return None


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
    async with httpx.AsyncClient(verify=True) as client:
        resp = await client.post(_url("/users"), headers=_headers(), json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        u = data.get("response", data)
        return _parse_user(u)


async def extend_subscription(uuid: str, duration_days: int) -> UserInfo:
    user = await get_user_by_uuid(uuid)
    if not user:
        raise Exception(f"User {uuid} not found")
    now = datetime.now(timezone.utc)
    base = user.expire_at if user.expire_at > now else now
    new_expire = base + timedelta(days=duration_days)
    payload = {
        "uuid": uuid,
        "expireAt": new_expire.isoformat(),
        "status": "ACTIVE",
    }
    async with httpx.AsyncClient(verify=True) as client:
        resp = await client.patch(_url("/users"), headers=_headers(), json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        u = data.get("response", data)
        return _parse_user(u)


async def set_expire_at(uuid: str, expire_at: datetime) -> Optional[UserInfo]:
    """Установить конкретную дату истечения (используется для бессрочного доступа)."""
    payload = {
        "uuid": uuid,
        "expireAt": expire_at.isoformat(),
        "status": "ACTIVE",
    }
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.patch(_url("/users"), headers=_headers(), json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            u = data.get("response", data)
            return _parse_user(u)
    except Exception:
        return None


async def update_user_limits(
    uuid: str,
    traffic_limit_gb: Optional[int] = None,
    device_limit: Optional[int] = None,
) -> Optional[UserInfo]:
    """Изменить лимиты трафика и устройств."""
    payload: dict = {"uuid": uuid}
    if traffic_limit_gb is not None:
        payload["trafficLimitBytes"] = traffic_limit_gb * 1024 ** 3
    if device_limit is not None:
        payload["hwidDeviceLimit"] = device_limit
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.patch(_url("/users"), headers=_headers(), json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            u = data.get("response", data)
            return _parse_user(u)
    except Exception:
        return None


async def set_user_status(uuid: str, status: str) -> Optional[UserInfo]:
    """Включить (ACTIVE) или выключить (DISABLED) пользователя."""
    payload = {"uuid": uuid, "status": status}
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.patch(_url("/users"), headers=_headers(), json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            u = data.get("response", data)
            return _parse_user(u)
    except Exception:
        return None


async def delete_panel_user(uuid: str) -> bool:
    """Удалить пользователя из панели."""
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.delete(_url(f"/users/{uuid}"), headers=_headers(), timeout=10)
            return resp.status_code in (200, 204)
    except Exception:
        return False


async def reset_user_traffic(uuid: str) -> bool:
    """Сброс использованного трафика."""
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.post(
                _url(f"/users/{uuid}/actions/reset-traffic"),
                headers=_headers(),
                timeout=10,
            )
            return resp.status_code in (200, 201)
    except Exception:
        return False


async def get_subscription_info(uuid: str) -> Optional[UserInfo]:
    return await get_user_by_uuid(uuid)


async def revoke_subscription(uuid: str) -> Optional[UserInfo]:
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.post(
                _url(f"/users/{uuid}/actions/revoke"),
                headers=_headers(),
                timeout=10,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            u = data.get("response", data)
            return _parse_user(u)
    except Exception:
        return None


# ── Squads ─────────────────────────────────────────────────────────────────

async def get_internal_squads() -> list[SquadInfo]:
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.get(_url("/internal-squads"), headers=_headers(), timeout=10)
            data = resp.json()
            squads = data.get("response", {}).get("internalSquads", [])
            return [
                SquadInfo(
                    uuid=s["uuid"],
                    name=s["name"],
                    members_count=s.get("info", {}).get("membersCount", 0),
                )
                for s in squads
            ]
    except Exception:
        return []


async def add_user_to_squad(user_uuid: str, squad_uuid: str) -> bool:
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.post(
                _url(f"/internal-squads/{squad_uuid}/bulk-actions/add-users"),
                headers=_headers(),
                json={"userUuids": [user_uuid]},
                timeout=10,
            )
            data = resp.json()
            return data.get("response", {}).get("eventSent", False)
    except Exception:
        return False


async def add_user_to_default_squad(
    user_uuid: str, tariff_squad_uuid: Optional[str] = None
) -> bool:
    squad_uuid = tariff_squad_uuid or settings.DEFAULT_SQUAD_UUID
    if not squad_uuid:
        return False
    return await add_user_to_squad(user_uuid, squad_uuid)


# ── HWID Devices ───────────────────────────────────────────────────────────

async def get_user_devices(user_uuid: str) -> list[HwidDevice]:
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.get(
                _url(f"/hwid/devices/{user_uuid}"), headers=_headers(), timeout=10
            )
            data = resp.json()
            devices = data.get("response", {}).get("devices", [])
            return [
                HwidDevice(
                    hwid=d["hwid"],
                    user_uuid=d["userUuid"],
                    platform=d.get("platform"),
                    os_version=d.get("osVersion"),
                    device_model=d.get("deviceModel"),
                    user_agent=d.get("userAgent"),
                    created_at=d.get("createdAt", ""),
                )
                for d in devices
            ]
    except Exception:
        return []


async def delete_user_device(user_uuid: str, hwid: str) -> bool:
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.post(
                _url("/hwid/devices/delete"),
                headers=_headers(),
                json={"userUuid": user_uuid, "hwid": hwid},
                timeout=10,
            )
            return resp.status_code == 200
    except Exception:
        return False


async def delete_all_user_devices(user_uuid: str) -> bool:
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.post(
                _url("/hwid/devices/delete-all"),
                headers=_headers(),
                json={"userUuid": user_uuid},
                timeout=10,
            )
            return resp.status_code == 200
    except Exception:
        return False


# ── Nodes ──────────────────────────────────────────────────────────────────

async def get_nodes() -> list[NodeInfo]:
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.get(_url("/nodes"), headers=_headers(), timeout=10)
            data = resp.json()
            nodes_raw = data.get("response", [])
            return [
                NodeInfo(
                    uuid=n["uuid"],
                    name=n["name"],
                    address=n["address"],
                    is_connected=n.get("isConnected", False),
                )
                for n in nodes_raw
            ]
    except Exception:
        return []


async def restart_node(node_uuid: str) -> bool:
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.post(
                _url(f"/nodes/{node_uuid}/restart"), headers=_headers(), timeout=10
            )
            return resp.status_code in (200, 201)
    except Exception:
        return False


# ── Inbounds & Hosts ───────────────────────────────────────────────────────

async def get_inbounds() -> list[InboundInfo]:
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.get(_url("/inbounds"), headers=_headers(), timeout=10)
            data = resp.json()
            raw = data.get("response", [])
            # response может быть списком или словарём с ключом
            if isinstance(raw, dict):
                raw = raw.get("inbounds", [])
            return [
                InboundInfo(
                    uuid=i["uuid"],
                    tag=i.get("tag", ""),
                    type=i.get("type", ""),
                    is_enabled=i.get("isEnabled", True),
                )
                for i in raw
            ]
    except Exception:
        return []


async def get_hosts() -> list[HostInfo]:
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.get(_url("/hosts"), headers=_headers(), timeout=10)
            data = resp.json()
            raw = data.get("response", [])
            if isinstance(raw, dict):
                raw = raw.get("hosts", [])
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
    except Exception:
        return []
