"""Fast TCP port scan of the Home Assistant host to locate SSH.

Non-destructive: connect-only, no payloads. Used because SSH was reported on
22/22222 but both refused connections.
"""

from __future__ import annotations

import asyncio
import sys

HOST = "homeassistant.local"
TIMEOUT = 0.4
CONCURRENCY = 400

#: Ports worth reporting if open (SSH variants + common services).
INTERESTING = {
    21, 22, 23, 25, 53, 80, 110, 143, 443, 445, 993, 995,
    1080, 1433, 1883, 2222, 2375, 2376, 3000, 3306, 3389,
    5000, 5432, 5555, 5900, 6379, 8000, 8008, 8080, 8081,
    8088, 8123, 8443, 8883, 9000, 9090, 9100, 9443, 10000,
    11211, 22222, 25565, 32400,
}


async def probe(sem: asyncio.Semaphore, port: int, found: list[int]) -> None:
    async with sem:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(HOST, port), timeout=TIMEOUT
            )
        except Exception:  # noqa: BLE001 - closed/filtered
            return
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        found.append(port)


async def main() -> int:
    sem = asyncio.Semaphore(CONCURRENCY)
    found: list[int] = []
    tasks = [asyncio.create_task(probe(sem, p, found)) for p in range(1, 65536)]
    await asyncio.gather(*tasks)
    open_ports = sorted(found)

    print(f"open ports on {HOST}: {open_ports or 'NONE'}")
    ssh_like = [p for p in open_ports if p in {22, 2222, 2200, 22222, 3222, 8022, 9022, 10022, 443}]
    print(f"ssh-like among them: {ssh_like or 'none'}")
    print(f"8123 (HA): {8123 in open_ports}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
