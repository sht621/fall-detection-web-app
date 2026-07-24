"""FastAPI application for camera events, videos, SSE, and a monitor page."""

from __future__ import annotations

import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.auth import AuthUser, authenticate, clear_session_cookie, create_session_cookie, get_current_user, require_csrf
from app.database import Database
from app.sse import SSEBroker

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOGGER = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATABASE = Database(os.getenv("DATABASE_PATH", "/data/fall_detection.sqlite3"))
VIDEO_DIR = Path(os.getenv("VIDEO_DIR", "/data/videos"))
SSE = SSEBroker()
VALID_REVIEWS = {"FALL_CONFIRMED", "NO_FALL"}
MAX_VIDEO_UPLOAD_BYTES = int(os.getenv("MAX_VIDEO_UPLOAD_BYTES", str(50 * 1024 * 1024)))


class DetectionInput(BaseModel):
    camera_id: str = Field(min_length=1, max_length=128)
    detected_at: str = Field(min_length=1, max_length=64)


class ReviewInput(BaseModel):
    review_result: str


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


@asynccontextmanager
async def lifespan(_: FastAPI):
    DATABASE.initialize()
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Fall Detection Monitor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def require_camera_token(authorization: str | None = Header(default=None)) -> None:
    expected_token = os.getenv("CAMERA_API_TOKEN")
    if not expected_token:
        LOGGER.error("CAMERA_API_TOKEN is not configured")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="camera API is not configured")
    if authorization != f"Bearer {expected_token}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid camera token")


def safe_video_filename(event_id: str) -> str:
    # Do not use the multipart filename supplied by the camera.
    return f"{re.sub(r'[^A-Za-z0-9_-]', '_', event_id)}.mp4"


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/monitor")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/monitor", include_in_schema=False)
def monitor() -> FileResponse:
    return FileResponse(STATIC_DIR / "monitor.html")


@app.get("/api/events")
async def events(request: Request, _: AuthUser = Depends(get_current_user)) -> StreamingResponse:
    return StreamingResponse(
        SSE.stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/login")
def login(credentials: LoginInput, response: Response) -> dict[str, str]:
    user = authenticate(credentials.username, credentials.password)
    create_session_cookie(response, user)
    return {"username": user.username, "csrf_token": user.csrf_token}


@app.post("/api/logout", dependencies=[Depends(require_csrf)])
def logout(response: Response) -> dict[str, str]:
    clear_session_cookie(response)
    return {"status": "ok"}


@app.get("/api/me")
def me(current_user: AuthUser = Depends(get_current_user)) -> dict[str, str]:
    return {"username": current_user.username, "csrf_token": current_user.csrf_token}


@app.put("/api/camera/detections/{event_id}", dependencies=[Depends(require_camera_token)])
async def register_detection(event_id: str, detection: DetectionInput) -> dict[str, object]:
    row, created = DATABASE.register_detection(event_id, detection.camera_id, detection.detected_at)
    if created:
        await SSE.publish("fall_detected", {"event_id": event_id})
    return {"detection": row, "created": created}


@app.put("/api/camera/detections/{event_id}/video", dependencies=[Depends(require_camera_token)])
async def upload_video(event_id: str, file: UploadFile = File(...)) -> dict[str, object]:
    if DATABASE.get_detection(event_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="detection not found")
    if file.content_type not in {"video/mp4", "application/octet-stream"}:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="MP4 upload required")

    DATABASE.mark_video_uploading(event_id)
    destination = VIDEO_DIR / safe_video_filename(event_id)
    temporary = destination.with_suffix(".part")
    try:
        total_size = 0
        with temporary.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                total_size += len(chunk)
                if total_size > MAX_VIDEO_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="video upload is too large",
                    )
                output.write(chunk)
        temporary.replace(destination)
        row = DATABASE.mark_video_ready(event_id, str(destination), destination.stat().st_size)
        if row is None:
            raise RuntimeError("detection disappeared before video update")
    except HTTPException:
        temporary.unlink(missing_ok=True)
        DATABASE.mark_video_failed(event_id)
        raise
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        DATABASE.mark_video_failed(event_id)
        LOGGER.exception("video upload failed for %s", event_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="video upload failed") from exc
    finally:
        await file.close()

    await SSE.publish("video_ready", {"event_id": event_id})
    return {"detection": row}


@app.get("/api/detections")
def list_detections(_: AuthUser = Depends(get_current_user)) -> list[dict[str, object]]:
    return DATABASE.list_detections()


@app.get("/api/detections/{event_id}")
def get_detection(event_id: str, _: AuthUser = Depends(get_current_user)) -> dict[str, object]:
    row = DATABASE.get_detection(event_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="detection not found")
    return row


@app.get("/api/detections/{event_id}/video")
def get_video(event_id: str, _: AuthUser = Depends(get_current_user)) -> FileResponse:
    row = DATABASE.get_detection(event_id)
    if row is None or row["video_status"] != "READY" or not row["video_path"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="video not ready")
    video_path = Path(str(row["video_path"]))
    if not video_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="video file not found")
    # Starlette FileResponse handles byte ranges, which is enough for these short review clips.
    return FileResponse(video_path, media_type="video/mp4", filename=f"{event_id}.mp4")


@app.patch("/api/detections/{event_id}/review")
def review_detection(
    event_id: str,
    review: ReviewInput,
    current_user: AuthUser = Depends(get_current_user),
    _: None = Depends(require_csrf),
) -> dict[str, object]:
    if review.review_result not in VALID_REVIEWS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid review result")
    row = DATABASE.review_detection(event_id, review.review_result, current_user.username)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="detection not found")
    return row
