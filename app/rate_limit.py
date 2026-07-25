"""Simple in-memory rate limiter for ForPost."""

import os
import time

from fastapi import HTTPException


class RateLimiter:
    """Token-bucket-style rate limiter. One bucket (single-user system)."""

    def __init__(self):
        self._requests: list[float] = []

    @property
    def limit(self) -> int:
        return int(os.environ.get("RATE_LIMIT_PER_MINUTE", "60"))

    def check(self) -> None:
        """Raise 429 if rate limit exceeded, otherwise record the request."""
        now = time.time()
        window_start = now - 60.0
        # Prune old entries
        self._requests = [t for t in self._requests if t > window_start]
        if len(self._requests) >= self.limit:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        self._requests.append(now)


rate_limiter = RateLimiter()
