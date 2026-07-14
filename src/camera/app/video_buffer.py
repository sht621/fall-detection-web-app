"""Raw-frame ring buffer used to build a clip around an event."""

from __future__ import annotations

from collections import deque
from pathlib import Path

import cv2
import numpy as np


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
        """Write roughly five seconds before and after an event using a replaceable codec path."""
        selected = [
            frame
            for timestamp, frame in self.frames
            if event_timestamp - self.pre_seconds <= timestamp <= event_timestamp + self.post_seconds
        ]
        if not selected:
            raise RuntimeError("no frames available for video clip")

        height, width = selected[0].shape[:2]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # TODO: switch this boundary to H.264/MP4 yuv420p after deployment codecs are decided.
        writer = cv2.VideoWriter(
            str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (width, height)
        )
        if not writer.isOpened():
            raise RuntimeError("could not initialize OpenCV VideoWriter")
        try:
            for frame in selected:
                if frame.shape[:2] != (height, width):
                    frame = cv2.resize(frame, (width, height))
                writer.write(frame)
        finally:
            writer.release()
        return len(selected)

