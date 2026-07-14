"""Camera process with a separate dummy trigger and future pose-detection hook."""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.api_client import APIClient
from app.fall_detector import FallDetector
from app.video_buffer import VideoBuffer

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOGGER = logging.getLogger(__name__)


def env_flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_camera(device: str) -> cv2.VideoCapture | None:
    source: int | str = int(device) if device.isdigit() else device
    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        LOGGER.warning("camera device %s is unavailable; using generated dummy frames", device)
        capture.release()
        return None
    return capture


def read_frame(capture: cv2.VideoCapture | None) -> np.ndarray:
    if capture is not None:
        ok, frame = capture.read()
        if ok:
            return frame
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    cv2.putText(frame, "Dummy camera frame", (180, 185), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (220, 220, 220), 2)
    return frame


def run_pose_inference(model: Any, frame: np.ndarray) -> tuple[Any, Any]:
    """Keep model invocation isolated so the dummy path never depends on YOLO."""
    if model is None:
        return None, None
    # TODO: select one person and extract bbox/keypoints for the rule engine.
    _ = model(frame, verbose=False)
    return None, None


def load_pose_model(model_path: str) -> Any:
    if env_flag("ENABLE_POSE_INFERENCE") is False:
        return None
    try:
        from ultralytics import YOLO

        return YOLO(model_path)
    except Exception:
        LOGGER.exception("pose model could not be loaded; continuing without inference")
        return None


def draw_local_preview(frame: np.ndarray, bbox: Any, keypoints: Any) -> np.ndarray:
    preview = frame.copy()
    # TODO: draw the actual bbox and skeleton once run_pose_inference extracts them.
    _ = (bbox, keypoints)
    cv2.putText(preview, "Camera preview", (16, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    return preview


def main() -> None:
    camera_id = os.getenv("CAMERA_ID", "camera-dev-1")
    device = os.getenv("CAMERA_DEVICE", "0")
    fps = float(os.getenv("PROCESS_FPS", "10"))
    simulate_fall = env_flag("SIMULATE_FALL")
    simulate_after = float(os.getenv("SIMULATE_FALL_AFTER_SECONDS", "5"))
    preview_enabled = env_flag("ENABLE_LOCAL_DISPLAY")
    output_dir = Path(os.getenv("CAMERA_OUTPUT_DIR", "/app/output"))

    api_client = APIClient(
        os.getenv("SERVER_BASE_URL", "http://server:8000"),
        os.getenv("CAMERA_API_TOKEN", ""),
    )
    detector = FallDetector()
    buffer = VideoBuffer(fps=fps)
    capture = open_camera(device)
    model = load_pose_model(os.getenv("MODEL_PATH", "yolo26n-pose.pt"))

    started_at = time.monotonic()
    simulated = False
    pending_event: tuple[str, float] | None = None
    frame_interval = 1.0 / fps
    try:
        while True:
            loop_started = time.monotonic()
            raw_frame = read_frame(capture)
            timestamp = time.monotonic()
            buffer.add_frame(timestamp, raw_frame)
            keypoints, bbox = run_pose_inference(model, raw_frame)

            keyboard_fall = False
            if preview_enabled:
                cv2.imshow("fall-detection-camera", draw_local_preview(raw_frame, bbox, keypoints))
                keyboard_fall = cv2.waitKey(1) & 0xFF == ord("f")

            simulated_fall = simulate_fall and not simulated and timestamp - started_at >= simulate_after
            detected = detector.update(keypoints, bbox, timestamp)
            if pending_event is None and (simulated_fall or keyboard_fall or detected):
                event_id = str(uuid.uuid4())
                detected_at = utc_now()
                try:
                    api_client.register_detection(event_id, camera_id, detected_at)
                    LOGGER.info("registered detection %s", event_id)
                    detector.mark_detected(timestamp)
                    pending_event = (event_id, timestamp)
                    simulated = simulated or simulated_fall
                except Exception:
                    LOGGER.exception("could not register detection %s", event_id)

            if pending_event is not None and timestamp - pending_event[1] >= buffer.post_seconds:
                event_id, event_timestamp = pending_event
                video_path = output_dir / f"{event_id}.mp4"
                try:
                    frame_count = buffer.write_clip(video_path, event_timestamp)
                    api_client.upload_video(event_id, video_path)
                    LOGGER.info("uploaded %s frames for detection %s", frame_count, event_id)
                except Exception:
                    LOGGER.exception("could not create or upload video for %s", event_id)
                pending_event = None

            elapsed = time.monotonic() - loop_started
            if elapsed < frame_interval:
                time.sleep(frame_interval - elapsed)
    except KeyboardInterrupt:
        LOGGER.info("camera process stopped")
    finally:
        api_client.close()
        if capture is not None:
            capture.release()
        if preview_enabled:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
