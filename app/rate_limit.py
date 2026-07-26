"""Simple in-memory rate limiter for ForPost."""

import os
import threading
import time

from fastapi import HTTPException


class RateLimiter:
    """Token-bucket-style rate limiter. One bucket (single-user system).

    Thread-safe: `check()` runs from FastAPI worker threads (execute is offloaded
    via run_in_threadpool) and from FastMCP's tool threads, so the shared window
    is mutated under a lock.
    """

    def __init__(self):
        self._requests: list[float] = []
        self._lock = threading.Lock()

    @property
    def limit(self) -> int:
        # Headroom for active job polling; nginx limit_req is the real flood gate.
        return int(os.environ.get("RATE_LIMIT_PER_MINUTE", "120"))

    def check(self) -> None:
        """Raise 429 if rate limit exceeded, otherwise record the request."""
        now = time.time()
        window_start = now - 60.0
        with self._lock:
            # Prune old entries
            self._requests = [t for t in self._requests if t > window_start]
            if len(self._requests) >= self.limit:
                raise HTTPException(status_code=429, detail="Rate limit exceeded")
            self._requests.append(now)


rate_limiter = RateLimiter()
