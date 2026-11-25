"""A dead man's switch: while the autopilot runs, it pings an outside monitor.

On 2026-11-04 the laptop running the stack lost power around midday. It crashed
going into hibernation and stayed off until 11-07. After it came back, the
pipeline could not see its files until a terminal started the WSL distro at
15:51. That came to about sixteen slots and three and a half days without a
video, and nothing said so: a Telegram alert needs the very machine that is
off. Only something outside can notice that a signal has stopped arriving.

Set HEALTHCHECK_URL to the ping URL of a check on healthchecks.io, or of
anything else that takes a GET. The autopilot pings it every
PING_EVERY_SECONDS while it can still reach its database. The monitor alerts
when the pings stop. Empty turns it off.
"""

import os
import time
from typing import Callable, Optional

import requests

from logstream import log

HEALTHCHECK_URL_ENV = "HEALTHCHECK_URL"
# Often enough for the monitor to notice within its grace period, rare enough
# not to matter: the autopilot ticks every minute.
PING_EVERY_SECONDS = 300
TIMEOUT_SECONDS = 10


class Heartbeat:
    def __init__(
        self,
        url: str,
        every: float = PING_EVERY_SECONDS,
        get: Callable[..., requests.Response] = requests.get,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = url.strip()
        self.every = every
        self._get = get
        self._clock = clock
        self._last: Optional[float] = None

    @classmethod
    def from_env(cls) -> "Heartbeat":
        return cls(os.getenv(HEALTHCHECK_URL_ENV, ""))

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def describe(self) -> str:
        if not self.enabled:
            return f"Heartbeat off ({HEALTHCHECK_URL_ENV} is empty)."
        return f"Heartbeat on: pinging the monitor every {self.every / 60:.0f} min."

    def beat(self, alive: Optional[Callable[[], bool]] = None) -> bool:
        """Pings the monitor if a ping is due. Returns whether one went out; never raises.

        `alive`, asked only when a ping is due, can withhold it: an autopilot
        that ticks but cannot reach its database is not working, and the
        monitor should hear nothing from it.

        The URL is the check's only credential, so it never reaches the log.
        """
        if not self.enabled:
            return False
        now = self._clock()
        if self._last is not None and now - self._last < self.every:
            return False
        # Counted before the request: a monitor that hangs, or a database that
        # is down, is tried again on the next interval, not on every tick.
        self._last = now
        try:
            if alive is not None and not alive():
                log("[!] Heartbeat withheld: the pipeline is not ready.", "warning")
                return False
        except Exception as err:
            log(f"[!] Heartbeat withheld: {type(err).__name__}.", "warning")
            return False
        try:
            response = self._get(self.url, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as err:
            log(f"[!] Heartbeat ping failed ({type(err).__name__}).", "warning")
            return False
        if response.status_code >= 400:
            log(f"[!] Heartbeat ping refused: HTTP {response.status_code}.", "warning")
            return False
        return True
