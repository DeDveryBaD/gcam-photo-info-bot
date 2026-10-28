"""Keep the Render free instance awake.

Render spins a free web service down after ~15 minutes without *incoming*
requests. Telegram long polling is outgoing traffic only, so it does not count
and the instance gets frozen mid-conversation. Nothing wakes it afterwards:
the queued updates sit in Telegram until some inbound request revives the box.

So the bot pings its own public health URL on a timer. The request leaves the
instance, passes Render's edge (which is what resets the idle timer) and comes
straight back. Cost: one HTTP GET every few minutes.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.request

log = logging.getLogger(__name__)

DEFAULT_INTERVAL = 300  # seconds; well under Render's 15-minute idle window


def start(url: str, interval: int = DEFAULT_INTERVAL) -> threading.Thread | None:
    """Start the pinger. Returns the thread, or None when there is nothing to ping."""
    if not url:
        return None
    stop_flag = threading.Event()

    def loop() -> None:
        missed = 0
        while not stop_flag.wait(interval):
            try:
                with urllib.request.urlopen(url, timeout=15) as resp:
                    resp.read()
                missed = 0
            except Exception as exc:  # noqa: BLE001 - a failed ping must never kill polling
                missed += 1
                if missed <= 2 or missed % 10 == 0:
                    log.warning("Keepalive ping to %s failed (%d in a row): %s",
                                url, missed, exc)

    thread = threading.Thread(target=loop, name="keepalive", daemon=True)
    thread.start()
    log.info("Keepalive pinging %s every %d s", url, interval)
    return thread


def remaining_seconds(deadline: float) -> int:
    return max(0, int(deadline - time.monotonic()))
