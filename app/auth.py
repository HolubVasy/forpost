"""API key authentication dependency."""

import os

from fastapi import Header, HTTPException


def get_api_key() -> str:
    """Return the configured API key from environment."""
    key = os.environ.get("API_KEY", "")
    if not key:
        raise RuntimeError("API_KEY environment variable is not set")
    return key


async def verify_api_key(x_api_key: str = Header(default=None)) -> str:
    """FastAPI dependency that validates the X-API-Key header."""
    if x_api_key is None:
        raise HTTPException(status_code=401, detail="Missing API key")
    if x_api_key != get_api_key():
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key
