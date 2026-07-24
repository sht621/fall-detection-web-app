"""Camera process for USB capture, YOLO pose inference, and local preview."""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
import uuid
from dataclasses import dataclass
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

WINDOW_NAME = "Fall Detection Camera"
KEYPOINT_CONFIDENCE = 0.25
SKELETON_EDGES = (
    (5, 6),
    (5, 7),
    (7, 9),
    (6, 8),
    (8, 10),
    (5, 11),
    (6, 12),
    (11, 12),
    (11, 13),
    (13, 15),
    (12, 14),
    (14, 16),
)


@dataclass
class PoseDetection:
    bbox: tuple[float, float, float, float]
    bbox_confidence: float
    keypoints_xy: np.ndarray
    keypoints_confidence: np.ndarray

    @property
    def area(self) -> float:
        x1, y1, x2, y2 = self.bbox
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def env_flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer: {value}") from exc


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number: {value}") from exc


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def cv_source(device: str) -> int | str:
    return int(device) if device.isdigit() else device


def backend_name(capture: cv2.VideoCapture) -> str:
    try:
        return capture.getBackendName()
    except cv2.error:
        return "unknown"


def open_camera(device: str, width: int, height: int) -> cv2.VideoCapture:
    source = cv_source(device)
    LOGGER.info("opening camera device=%s requested_width=%s requested_height=%s", device, width, height)

    capture = cv2.VideoCapture(source, cv2.CAP_V4L2)
    if not capture.isOpened():
        LOGGER.warning("camera open with V4L2 failed; retrying with OpenCV default backend")
        capture.release()
        capture = cv2.VideoCapture(source)

    if not capture.isOpened():
        capture.release()
        raise RuntimeError(
            f"could not open camera device {device}. Check HOST_CAMERA_DEVICE, CAMERA_DEVICE, "
            "container device mapping, and host permissions."
        )

    capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    actual_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    actual_fps = capture.get(cv2.CAP_PROP_FPS)
    LOGGER.info("camera opened=true backend=%s", backend_name(capture))
    LOGGER.info("camera actual_width=%s actual_height=%s reported_fps=%.2f", actual_width, actual_height, actual_fps)
    return capture


def read_frame(capture: cv2.VideoCapture, failure_count: int, max_failures: int) -> tuple[np.ndarray | None, int]:
    ok, frame = capture.read()
    if ok and frame is not None:
        if failure_count:
            LOGGER.info("camera read recovered after %s failures", failure_count)
        return frame, 0

    failure_count += 1
    LOGGER.warning("camera read failed %s/%s", failure_count, max_failures)
    if failure_count >= max_failures:
        raise RuntimeError(f"camera read failed {failure_count} times in a row")
    time.sleep(0.1)
    return None, failure_count


def log_torch_runtime(pose_device: str) -> str:
    try:
        import torch
    except Exception as exc:
        raise RuntimeError("PyTorch is required in the camera container") from exc

    LOGGER.info("torch version=%s cuda_version=%s", torch.__version__, torch.version.cuda)
    cuda_available = torch.cuda.is_available()
    LOGGER.info("torch.cuda.is_available()=%s", cuda_available)
    if cuda_available:
        for index in range(torch.cuda.device_count()):
            LOGGER.info("cuda device %s=%s", index, torch.cuda.get_device_name(index))

    if pose_device.strip().lower() == "cpu":
        LOGGER.warning("POSE_DEVICE=cpu was explicitly requested; YOLO inference will run on CPU")
        return "cpu"

    if not cuda_available:
        LOGGER.warning("CUDA is not available. Set POSE_DEVICE=cpu to allow CPU inference intentionally.")
        raise RuntimeError("CUDA is not available and POSE_DEVICE is not cpu")

    LOGGER.info("YOLO inference device=%s", pose_device)
    return pose_device


def load_pose_model(model_path: str) -> Any:
    try:
        import ultralytics
        from ultralytics import YOLO
    except Exception as exc:
        raise RuntimeError("Ultralytics is required in the camera container") from exc

    model_ref = model_path.strip() or "yolo26n-pose.pt"
    path = Path(model_ref)
    if path.is_absolute() or "/" in model_ref:
        if path.exists():
            LOGGER.info("pose model path exists; loading from %s", model_ref)
        else:
            LOGGER.warning(
                "pose model path does not exist: %s. Ultralytics will still try to resolve it; "
                "use MODEL_PATH=yolo26n-pose.pt for automatic first-run download.",
                model_ref,
            )
    else:
        LOGGER.info("loading pose model %s; Ultralytics may download it on first use", model_ref)

    LOGGER.info("ultralytics version=%s", getattr(ultralytics, "__version__", "unknown"))
    model = YOLO(model_ref)
    LOGGER.info("pose model loaded from %s", model_ref)
    return model


def run_pose_inference(
    model: Any,
    frame: np.ndarray,
    device: str,
    confidence: float,
    image_size: int,
) -> tuple[list[PoseDetection], float]:
    started = time.perf_counter()
    results = model.predict(
        source=frame,
        conf=confidence,
        imgsz=image_size,
        device=device,
        verbose=False,
    )
    inference_ms = (time.perf_counter() - started) * 1000.0
    if not results:
        return [], inference_ms
    return extract_pose_detections(results[0]), inference_ms


def as_numpy(value: Any) -> np.ndarray:
    if value is None:
        return np.empty((0,))
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        return value.numpy()
    return np.asarray(value)


def extract_pose_detections(result: Any) -> list[PoseDetection]:
    boxes = getattr(result, "boxes", None)
    keypoints = getattr(result, "keypoints", None)
    if boxes is None or keypoints is None:
        return []

    xyxy = as_numpy(getattr(boxes, "xyxy", None))
    box_conf = as_numpy(getattr(boxes, "conf", None))
    kpt_xy = as_numpy(getattr(keypoints, "xy", None))
    kpt_conf = as_numpy(getattr(keypoints, "conf", None))

    if xyxy.size == 0 or kpt_xy.size == 0:
        return []
    if kpt_conf.size == 0:
        kpt_conf = np.ones(kpt_xy.shape[:2], dtype=np.float32)

    count = min(len(xyxy), len(kpt_xy))
    detections: list[PoseDetection] = []
    for index in range(count):
        confidence = float(box_conf[index]) if index < len(box_conf) else 0.0
        detections.append(
            PoseDetection(
                bbox=tuple(float(v) for v in xyxy[index][:4]),
                bbox_confidence=confidence,
                keypoints_xy=np.asarray(kpt_xy[index], dtype=np.float32),
                keypoints_confidence=np.asarray(kpt_conf[index], dtype=np.float32),
            )
        )
    return detections


def select_primary_person(detections: list[PoseDetection]) -> PoseDetection | None:
    """Current policy: use the largest bbox for future fall rules; no tracking or IDs."""
    if not detections:
        return None
    return max(detections, key=lambda detection: detection.area)


def draw_pose(display_frame: np.ndarray, detection: PoseDetection, primary: bool) -> None:
    x1, y1, x2, y2 = (int(round(v)) for v in detection.bbox)
    color = (0, 220, 255) if primary else (80, 200, 80)
    cv2.rectangle(display_frame, (x1, y1), (x2, y2), color, 2)
    cv2.putText(
        display_frame,
        f"{detection.bbox_confidence:.2f}",
        (x1, max(20, y1 - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        color,
        2,
    )

    points = detection.keypoints_xy
    scores = detection.keypoints_confidence
    for start, end in SKELETON_EDGES:
        if start >= len(points) or end >= len(points):
            continue
        if scores[start] < KEYPOINT_CONFIDENCE or scores[end] < KEYPOINT_CONFIDENCE:
            continue
        cv2.line(display_frame, tuple(points[start].astype(int)), tuple(points[end].astype(int)), color, 2)

    for index, point in enumerate(points):
        if index >= len(scores) or scores[index] < KEYPOINT_CONFIDENCE:
            continue
        cv2.circle(display_frame, tuple(point.astype(int)), 3, (255, 255, 255), -1)


def draw_status(
    display_frame: np.ndarray,
    camera_id: str,
    processing_fps: float,
    inference_ms: float,
    detection_count: int,
    pose_device: str,
) -> None:
    lines = [
        f"camera: {camera_id}",
        f"process fps: {processing_fps:.1f}",
        f"yolo: {inference_ms:.1f} ms",
        f"persons: {detection_count}",
        f"device: {pose_device}",
    ]
    y = 24
    for line in lines:
        cv2.putText(display_frame, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 0, 0), 4)
        cv2.putText(display_frame, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (240, 240, 240), 2)
        y += 24


def draw_local_preview(
    raw_frame: np.ndarray,
    detections: list[PoseDetection],
    primary: PoseDetection | None,
    camera_id: str,
    processing_fps: float,
    inference_ms: float,
    pose_device: str,
) -> np.ndarray:
    display_frame = raw_frame.copy()
    for detection in detections:
        draw_pose(display_frame, detection, detection is primary)
    draw_status(display_frame, camera_id, processing_fps, inference_ms, len(detections), pose_device)
    return display_frame


def ensure_display_available(show_window: bool) -> None:
    if show_window and not os.getenv("DISPLAY"):
        LOGGER.warning("SHOW_WINDOW=true but DISPLAY is not set; OpenCV imshow may fail. Set SHOW_WINDOW=false for headless.")


def handle_window(display_frame: np.ndarray) -> bool:
    cv2.imshow(WINDOW_NAME, display_frame)
    key = cv2.waitKey(1) & 0xFF
    if key in (ord("q"), 27):
        return True
    return False


def main() -> None:
    camera_id = os.getenv("CAMERA_ID", "camera-dev-1")
    camera_device = os.getenv("CAMERA_DEVICE", "/dev/video0")
    camera_width = env_int("CAMERA_WIDTH", 640)
    camera_height = env_int("CAMERA_HEIGHT", 480)
    process_fps = env_float("PROCESS_FPS", 10.0)
    model_path = os.getenv("MODEL_PATH", "yolo26n-pose.pt")
    pose_device_env = os.getenv("POSE_DEVICE", "0")
    pose_confidence = env_float("POSE_CONFIDENCE", 0.5)
    pose_image_size = env_int("POSE_IMAGE_SIZE", 640)
    show_window = env_flag("SHOW_WINDOW", True)
    max_read_failures = env_int("MAX_CAMERA_READ_FAILURES", 10)
    fall_candidate_seconds = env_float("FALL_CANDIDATE_SECONDS", 1.0)
    fall_bbox_aspect_threshold = env_float("FALL_BBOX_ASPECT_THRESHOLD", 1.15)
    fall_torso_angle_threshold = env_float("FALL_TORSO_ANGLE_THRESHOLD", 60.0)
    status_log_seconds = env_float("STATUS_LOG_SECONDS", 5.0)
    output_dir = Path(os.getenv("CAMERA_OUTPUT_DIR", "/app/output"))

    if process_fps <= 0:
        raise RuntimeError("PROCESS_FPS must be greater than 0")

    api_client = APIClient(os.getenv("SERVER_BASE_URL", "http://server:8000"), os.getenv("CAMERA_API_TOKEN", ""))
    capture: cv2.VideoCapture | None = None
    stop_requested = False

    def request_shutdown(signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True
        LOGGER.info("shutdown requested by signal %s", signum)

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    try:
        ensure_display_available(show_window)
        pose_device = log_torch_runtime(pose_device_env)
        model = load_pose_model(model_path)
        capture = open_camera(camera_device, camera_width, camera_height)

        detector = FallDetector(
            candidate_seconds=fall_candidate_seconds,
            bbox_aspect_threshold=fall_bbox_aspect_threshold,
            torso_angle_threshold=fall_torso_angle_threshold,
        )
        buffer = VideoBuffer(fps=process_fps)
        next_process_at = time.perf_counter()
        frame_interval = 1.0 / process_fps
        read_failures = 0
        pending_event: tuple[str, float] | None = None
        processing_fps = 0.0
        last_processed_at: float | None = None
        last_status_log_at = time.monotonic()

        LOGGER.info(
            "camera processing started camera_id=%s process_fps=%.2f show_window=%s",
            camera_id,
            process_fps,
            show_window,
        )
        while not stop_requested:
            now = time.perf_counter()
            if now < next_process_at:
                time.sleep(min(0.02, next_process_at - now))
                continue
            next_process_at = max(next_process_at + frame_interval, now)

            raw_frame, read_failures = read_frame(capture, read_failures, max_read_failures)
            if raw_frame is None:
                continue

            timestamp = time.monotonic()
            if last_processed_at is not None:
                delta = timestamp - last_processed_at
                if delta > 0:
                    measured = 1.0 / delta
                    processing_fps = measured if processing_fps == 0.0 else (processing_fps * 0.85 + measured * 0.15)
            last_processed_at = timestamp

            buffer.add_frame(timestamp, raw_frame)
            try:
                detections, inference_ms = run_pose_inference(model, raw_frame, pose_device, pose_confidence, pose_image_size)
            except Exception:
                LOGGER.exception("pose inference failed")
                detections, inference_ms = [], 0.0

            primary = select_primary_person(detections)
            keypoints = (
                {"xy": primary.keypoints_xy, "confidence": primary.keypoints_confidence}
                if primary is not None
                else None
            )
            bbox = primary.bbox if primary is not None else None

            detected = detector.update(keypoints=keypoints, bbox=bbox, timestamp=timestamp)

            if timestamp - last_status_log_at >= status_log_seconds:
                LOGGER.info(
                    "pose loop status process_fps=%.1f inference_ms=%.1f persons=%s device=%s",
                    processing_fps,
                    inference_ms,
                    len(detections),
                    pose_device,
                )
                last_status_log_at = timestamp

            if show_window:
                display_frame = draw_local_preview(
                    raw_frame,
                    detections,
                    primary,
                    camera_id,
                    processing_fps,
                    inference_ms,
                    pose_device,
                )
                try:
                    should_stop = handle_window(display_frame)
                except cv2.error as exc:
                    raise RuntimeError("OpenCV window display failed. Check X11 settings or set SHOW_WINDOW=false.") from exc
                if should_stop:
                    LOGGER.info("normal shutdown requested from keyboard")
                    break

            if pending_event is None and detected:
                event_id = str(uuid.uuid4())
                try:
                    api_client.register_detection(event_id, camera_id, utc_now())
                    LOGGER.info("registered detection %s", event_id)
                    detector.mark_detected(timestamp)
                    pending_event = (event_id, timestamp)
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

    finally:
        api_client.close()
        if capture is not None:
            capture.release()
            LOGGER.info("camera released")
        if show_window:
            cv2.destroyAllWindows()
            LOGGER.info("OpenCV windows destroyed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        LOGGER.error("%s", exc)
        sys.exit(1)
