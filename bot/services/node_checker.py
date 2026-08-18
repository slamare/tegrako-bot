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
    address = address.strip()
    if address.startswith("["):
        host, _, rest = address[1:].partition("]")
        if rest.startswith(":") and rest[1:].isdigit():
            return host, int(rest[1:])
        return host, 443
    if address.count(":") > 1:
        return address, 443
    host, _, port_str = address.rpartition(":")
    if host and port_str.isdigit():
        return host, int(port_str)
    return address, 443


async def _check_one(node, semaphore: asyncio.Semaphore):
    async with semaphore:
        host, port = _split_address(node.address)
        is_up, latency = await _tcp_check(host, port)
        return node, is_up, latency


async def check_nodes_once():
    from db.database import async_session_maker

    nodes = await remnawave.get_nodes()
    semaphore = asyncio.Semaphore(10)
    results = await asyncio.gather(
        *[_check_one(node, semaphore) for node in nodes],
        return_exceptions=True,
    )

    async with async_session_maker() as session:
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Node check failed: {result!r}")
                continue
            node, is_up, latency = result
            await dal.log_node_check(session, node.uuid, node.name, is_up, latency)


async def node_checker_loop():
    await asyncio.sleep(15)
    while True:
        try:
            await check_nodes_once()
        except Exception as e:
            logger.error(f"Node checker error: {e}")
        await asyncio.sleep(settings.NODE_CHECK_INTERVAL_MINUTES * 60)
