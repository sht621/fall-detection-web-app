"""Development authentication seam for later session-cookie and CSRF support."""

from __future__ import annotations

import os

from fastapi import HTTPException, status


def auth_disabled() -> bool:
    return os.getenv("AUTH_DISABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def get_current_user() -> str:
    if auth_disabled():
        # TODO: replace this development identity with session-cookie validation and CSRF checks.
        return "development-monitor"
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="authentication is not configured",
    )

