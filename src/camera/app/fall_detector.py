"""A timestamp-based placeholder for the future fall-detection rules."""

from __future__ import annotations

from enum import Enum
from typing import Any


class FallState(str, Enum):
    NORMAL = "NORMAL"
    CANDIDATE = "CANDIDATE"
    FALL_DETECTED = "FALL_DETECTED"
    COOLDOWN = "COOLDOWN"


class FallDetector:
    def __init__(self, cooldown_seconds: float = 10.0) -> None:
        self.state = FallState.NORMAL
        self.cooldown_seconds = cooldown_seconds
        self.last_detected_at: float | None = None

    def update(self, keypoints: Any, bbox: Any, timestamp: float) -> bool:
        """Return True only when the future rule engine confirms a new fall."""
        if self.state is FallState.COOLDOWN:
            if self.last_detected_at is not None and timestamp - self.last_detected_at >= self.cooldown_seconds:
                self.state = FallState.NORMAL
            return False

        # TODO: derive candidate signals from shoulder/hip positions, bbox aspect
        # ratio, and hip descent. Use timestamps for dwell times rather than frame
        # counts so the decision remains stable when the effective FPS varies.
        _ = (keypoints, bbox, timestamp)
        return False

    def mark_detected(self, timestamp: float) -> None:
        """Used by the separate simulation path until real rules are implemented."""
        self.last_detected_at = timestamp
        self.state = FallState.COOLDOWN

