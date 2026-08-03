import asyncio
import logging
import time

from db import dal
from bot.services import remnawave
from config.settings import settings

logger = logging.getLogger(__name__)


async def _tcp_check(host: str, port: int, timeout: float = 5.0) -> tuple[bool, int | None]:
    start = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True, int((time.monotonic() - start) * 1000)
    except Exception:
        return False, None


def _split_address(address: str) -> tuple[str, int]:
    host, _, port_str = address.rpartition(":")
    if host and port_str.isdigit():
        return host, int(port_str)
    return address, 443


async def check_nodes_once():
    from db.database import async_session_maker

    nodes = await remnawave.get_nodes()
    async with async_session_maker() as session:
        for node in nodes:
            host, port = _split_address(node.address)
            is_up, latency = await _tcp_check(host, port)
            await dal.log_node_check(session, node.uuid, node.name, is_up, latency)


async def node_checker_loop():
    await asyncio.sleep(15)
    while True:
        try:
            await check_nodes_once()
        except Exception as e:
            logger.error(f"Node checker error: {e}")
        await asyncio.sleep(settings.NODE_CHECK_INTERVAL_MINUTES * 60)
