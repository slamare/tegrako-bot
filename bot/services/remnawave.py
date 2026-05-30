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
            resp = await client.get(_url(f"/users/by-username/{username}"), headers=_headers(), timeout=10)
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
        "expireAt": new_expire.isoformat(),
        "status": "ACTIVE",
    }
    async with httpx.AsyncClient(verify=True) as client:
        resp = await client.put(_url(f"/users/{uuid}"), headers=_headers(), json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        u = data.get("response", data)
        return _parse_user(u)


async def get_subscription_info(uuid: str) -> Optional[UserInfo]:
    return await get_user_by_uuid(uuid)


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
            resp = await client.post(_url(f"/nodes/{node_uuid}/restart"), headers=_headers(), timeout=10)
            return resp.status_code in (200, 201)
    except Exception:
        return False
