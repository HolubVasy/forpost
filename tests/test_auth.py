"""Tests for app/auth.py - API key authentication."""

import os

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from app.auth import verify_api_key

# Minimal app for testing the auth dependency in isolation
app = FastAPI()


@app.get("/protected")
async def protected_route(api_key: str = Depends(verify_api_key)):
    return {"status": "ok"}


@pytest.fixture(autouse=True)
def set_api_key(monkeypatch):
    """Set a known API key for all tests."""
    monkeypatch.setenv("API_KEY", "test-secret-key")


class TestAuth:
    def test_missing_api_key_returns_401(self):
        client = TestClient(app)
        response = client.get("/protected")
        assert response.status_code == 401
        assert "Missing" in response.json()["detail"]

    def test_invalid_api_key_returns_401(self):
        client = TestClient(app)
        response = client.get("/protected", headers={"X-API-Key": "wrong-key"})
        assert response.status_code == 401
        assert "Invalid" in response.json()["detail"]

    def test_valid_api_key_passes(self):
        client = TestClient(app)
        response = client.get(
            "/protected", headers={"X-API-Key": "test-secret-key"}
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_empty_api_key_header_returns_401(self):
        client = TestClient(app)
        response = client.get("/protected", headers={"X-API-Key": ""})
        assert response.status_code == 401
        assert "Invalid" in response.json()["detail"]
