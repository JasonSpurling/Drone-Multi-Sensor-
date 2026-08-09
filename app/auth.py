"""Shared-secret API key check, active only when DRONE_API_KEY is set."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException

from app.config import API_KEY


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if not API_KEY:
        return
    if x_api_key is None or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header")
