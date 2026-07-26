"""API key authentication dependency."""

import hmac
import os

from fastapi import Header, HTTPException


def get_api_key() -> str:
    """Return the configured API key from environment."""
    key = os.environ.get("API_KEY", "")
    if not key:
        raise RuntimeError("API_KEY environment variable is not set")
    return key


def keys_match(provided: str, expected: str) -> bool:
    """Constant-time comparison to avoid a timing side-channel on the key."""
    return hmac.compare_digest(provided, expected)


async def verify_api_key(x_api_key: str = Header(default=None)) -> str:
    """FastAPI dependency that validates the X-API-Key header."""
    if x_api_key is None:
        raise HTTPException(status_code=401, detail="Missing API key")
    if not keys_match(x_api_key, get_api_key()):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key
