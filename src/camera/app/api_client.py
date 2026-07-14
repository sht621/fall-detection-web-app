"""Small HTTP client for the camera-to-server contract."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import httpx

LOGGER = logging.getLogger(__name__)


class APIClient:
    def __init__(self, base_url: str, token: str, retries: int = 3) -> None:
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self.client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(15.0, connect=5.0),
        )

    def close(self) -> None:
        self.client.close()

    def register_detection(
        self, event_id: str, camera_id: str, detected_at: str
    ) -> dict[str, Any]:
        return self._put_json(
            f"/api/camera/detections/{event_id}",
            {"camera_id": camera_id, "detected_at": detected_at},
        )

    def upload_video(self, event_id: str, video_path: Path) -> dict[str, Any]:
        for attempt in range(1, self.retries + 1):
            try:
                with video_path.open("rb") as video_file:
                    response = self.client.put(
                        f"/api/camera/detections/{event_id}/video",
                        files={"file": ("clip.mp4", video_file, "video/mp4")},
                    )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, OSError) as exc:
                if attempt == self.retries:
                    raise
                LOGGER.warning("video upload attempt %s/%s failed: %s", attempt, self.retries, exc)
                time.sleep(attempt)
        raise RuntimeError("unreachable retry loop")

    def _put_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(1, self.retries + 1):
            try:
                response = self.client.put(path, json=payload)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as exc:
                if attempt == self.retries:
                    raise
                LOGGER.warning("request attempt %s/%s failed: %s", attempt, self.retries, exc)
                time.sleep(attempt)
        raise RuntimeError("unreachable retry loop")

