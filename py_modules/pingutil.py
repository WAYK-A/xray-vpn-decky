"""Асинхронный TCP-ping списка серверов."""

from __future__ import annotations

import asyncio
import time
from typing import Any


async def tcp_ping(host: str, port: int, timeout: float = 3.0) -> int | None:
    """Время TCP-подключения в мс, либо None при недоступности."""
    start = time.perf_counter()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        elapsed = int((time.perf_counter() - start) * 1000)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return elapsed
    except Exception:  # noqa: BLE001
        return None


async def ping_servers(
    servers: list[dict[str, Any]], timeout: float = 3.0, concurrency: int = 32
) -> dict[str, int | None]:
    """Пропинговать всех серверов параллельно. Возвращает {server_id: ping_ms|None}."""
    sem = asyncio.Semaphore(concurrency)

    async def one(server: dict[str, Any]) -> tuple[str, int | None]:
        async with sem:
            ping = await tcp_ping(server["host"], int(server["port"]), timeout)
            return str(server["id"]), ping

    results = await asyncio.gather(*(one(s) for s in servers))
    return dict(results)
