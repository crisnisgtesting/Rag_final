"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Request

from app.config import Settings, get_settings


def get_app_settings(request: Request) -> Settings:
    """Return the Settings bound to this application instance."""
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else get_settings()
