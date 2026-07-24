"""Timestamp-based fall-detection rules kept intentionally simple."""

from __future__ import annotations

import math
from enum import Enum
from typing import Any

import numpy as np


class FallState(str, Enum):
    NORMAL = "NORMAL"
    CANDIDATE = "CANDIDATE"
    FALL_DETECTED = "FALL_DETECTED"
    COOLDOWN = "COOLDOWN"


class FallDetector:
    def __init__(
        self,
        candidate_seconds: float = 1.0,
        cooldown_seconds: float = 10.0,
        bbox_aspect_threshold: float = 1.15,
        torso_angle_threshold: float = 60.0,
        keypoint_confidence: float = 0.25,
    ) -> None:
        self.state = FallState.NORMAL
        self.candidate_seconds = candidate_seconds
        self.cooldown_seconds = cooldown_seconds
        self.bbox_aspect_threshold = bbox_aspect_threshold
        self.torso_angle_threshold = torso_angle_threshold
        self.keypoint_confidence = keypoint_confidence
        self.candidate_started_at: float | None = None
        self.last_detected_at: float | None = None

    def update(self, keypoints: Any, bbox: Any, timestamp: float) -> bool:
        """Return True once when the simple rules remain suspicious long enough."""
        if self.state is FallState.COOLDOWN:
            if self.last_detected_at is not None and timestamp - self.last_detected_at >= self.cooldown_seconds:
                self.state = FallState.NORMAL
                self.candidate_started_at = None
            return False

        suspicious = self._is_suspicious_pose(keypoints, bbox)
        if not suspicious:
            self.state = FallState.NORMAL
            self.candidate_started_at = None
            return False

        if self.state is FallState.NORMAL:
            self.state = FallState.CANDIDATE
            self.candidate_started_at = timestamp
            return False

        if self.state is FallState.CANDIDATE and self.candidate_started_at is not None:
            if timestamp - self.candidate_started_at >= self.candidate_seconds:
                self.state = FallState.FALL_DETECTED
                self.last_detected_at = timestamp
                return True

        return False

    def mark_detected(self, timestamp: float) -> None:
        """Used by the separate simulation path until real rules are implemented."""
        self.last_detected_at = timestamp
        self.candidate_started_at = None
        self.state = FallState.COOLDOWN

    def _is_suspicious_pose(self, keypoints: Any, bbox: Any) -> bool:
        if bbox is None:
            return False

        x1, y1, x2, y2 = (float(value) for value in bbox)
        width = max(0.0, x2 - x1)
        height = max(1.0, y2 - y1)
        bbox_aspect = width / height
        if bbox_aspect >= self.bbox_aspect_threshold:
            return True

        xy, confidence = self._split_keypoints(keypoints)
        torso_angle = self._torso_angle_from_vertical(xy, confidence)
        return torso_angle is not None and torso_angle >= self.torso_angle_threshold

    def _split_keypoints(self, keypoints: Any) -> tuple[np.ndarray, np.ndarray | None]:
        if keypoints is None:
            return np.empty((0, 2), dtype=np.float32), None
        if isinstance(keypoints, dict):
            xy = np.asarray(keypoints.get("xy", []), dtype=np.float32)
            confidence_value = keypoints.get("confidence")
            confidence = None if confidence_value is None else np.asarray(confidence_value, dtype=np.float32)
            return xy, confidence
        return np.asarray(keypoints, dtype=np.float32), None

    def _torso_angle_from_vertical(self, xy: np.ndarray, confidence: np.ndarray | None) -> float | None:
        required = (5, 6, 11, 12)
        if xy.shape[0] <= max(required):
            return None
        if confidence is not None and any(confidence[index] < self.keypoint_confidence for index in required):
            return None

        shoulder_mid = (xy[5] + xy[6]) / 2.0
        hip_mid = (xy[11] + xy[12]) / 2.0
        dx = float(hip_mid[0] - shoulder_mid[0])
        dy = float(hip_mid[1] - shoulder_mid[1])
        if dx == 0.0 and dy == 0.0:
            return None

        # Later calibration can add hip descent and low-posture dwell; this keeps
        # the first rule understandable and timestamp based.
        return math.degrees(math.atan2(abs(dx), abs(dy)))
