"""Raw-frame ring buffer used to build a clip around an event."""

from __future__ import annotations

import logging
import subprocess
from collections import deque
from pathlib import Path

import cv2
import numpy as np

LOGGER = logging.getLogger(__name__)


class VideoBuffer:
    def __init__(self, fps: float = 10.0, pre_seconds: float = 5.0, post_seconds: float = 5.0) -> None:
        self.fps = fps
        self.pre_seconds = pre_seconds
        self.post_seconds = post_seconds
        self.frames: deque[tuple[float, np.ndarray]] = deque()

    def add_frame(self, timestamp: float, frame: np.ndarray) -> None:
        """Store the unannotated frame; display overlays are made on a separate copy."""
        self.frames.append((timestamp, frame.copy()))
        cutoff = timestamp - (self.pre_seconds + self.post_seconds + 2.0)
        while self.frames and self.frames[0][0] < cutoff:
            self.frames.popleft()

    def write_clip(self, output_path: Path, event_timestamp: float) -> int:
        """Write roughly five seconds before and after an event."""
        selected = [
            frame
            for timestamp, frame in self.frames
            if event_timestamp - self.pre_seconds <= timestamp <= event_timestamp + self.post_seconds
        ]
        if not selected:
            raise RuntimeError("no frames available for video clip")

        height, width = selected[0].shape[:2]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.unlink(missing_ok=True)

        try:
            self._write_h264_with_ffmpeg(selected, output_path, width, height)
        except (FileNotFoundError, RuntimeError, BrokenPipeError) as exc:
            LOGGER.warning("ffmpeg H.264 output failed; falling back to OpenCV mp4v: %s", exc)
            output_path.unlink(missing_ok=True)
            self._write_mp4v_with_opencv(selected, output_path, width, height)
        return len(selected)

    def _write_mp4v_with_opencv(
        self,
        frames: list[np.ndarray],
        output_path: Path,
        width: int,
        height: int,
    ) -> None:
        writer = cv2.VideoWriter(
            str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (width, height)
        )
        if not writer.isOpened():
            raise RuntimeError("could not initialize OpenCV VideoWriter")
        try:
            for frame in frames:
                if frame.shape[:2] != (height, width):
                    frame = cv2.resize(frame, (width, height))
                writer.write(frame)
        finally:
            writer.release()

    def _write_h264_with_ffmpeg(
        self,
        frames: list[np.ndarray],
        output_path: Path,
        width: int,
        height: int,
    ) -> None:
        command = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{width}x{height}",
            "-r",
            str(self.fps),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        assert process.stdin is not None
        try:
            for frame in frames:
                if frame.shape[:2] != (height, width):
                    frame = cv2.resize(frame, (width, height))
                process.stdin.write(np.ascontiguousarray(frame).tobytes())
        finally:
            process.stdin.close()

        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(stderr.strip() or f"ffmpeg exited with status {return_code}")
