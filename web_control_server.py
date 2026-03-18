from __future__ import annotations

import atexit
import os
import threading
import time
from collections import deque
from typing import Deque, Dict, Generator, Optional, Set, Tuple

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.wsgi import WSGIMiddleware
from flask import Flask, Response, render_template
from pydantic import BaseModel, Field

from audit_logger import AuditLogger
from gun_controller import GunConfig, GunController


CAMERA_INDEX = 0
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720
CAMERA_FPS = 30
JPEG_QUALITY = 85

OPERATOR_KEY = os.getenv("RWS_OPERATOR_KEY", "").strip()
OBSERVER_KEY = os.getenv("RWS_OBSERVER_KEY", "").strip()

# Backward compatibility with earlier single-key setup.
LEGACY_KEY = os.getenv("RWS_API_KEY", "").strip()
if LEGACY_KEY and not OPERATOR_KEY:
    OPERATOR_KEY = LEGACY_KEY

AUTH_REQUIRED = bool(OPERATOR_KEY or OBSERVER_KEY)
RATE_LIMIT_PER_MIN = int(os.getenv("RWS_RATE_LIMIT_PER_MIN", "120"))

ROLE_POLICIES: Dict[Tuple[str, str], Set[str]] = {
    ("GET", "/api/state"): {"operator", "observer"},
    ("POST", "/api/aim"): {"operator"},
    ("POST", "/api/angles"): {"operator"},
    ("POST", "/api/center"): {"operator"},
    ("POST", "/api/fire"): {"operator"},
    ("POST", "/api/self-test"): {"operator"},
    ("GET", "/api/audit"): {"operator"},
}


class SimpleRateLimiter:
    def __init__(self, limit_per_minute: int):
        self.limit = max(1, limit_per_minute)
        self.window_seconds = 60.0
        self._buckets: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> Tuple[bool, int]:
        now = time.time()
        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            while bucket and (now - bucket[0]) > self.window_seconds:
                bucket.popleft()

            if len(bucket) >= self.limit:
                retry_after = int(self.window_seconds - (now - bucket[0]))
                return False, max(retry_after, 1)

            bucket.append(now)
            return True, 0


class CameraStream:
    def __init__(self, camera_index: int = CAMERA_INDEX):
        self.camera_index = camera_index
        self._capture: Optional[cv2.VideoCapture] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._last_error: Optional[str] = None

    def start(self):
        if self._running:
            return

        self._capture = cv2.VideoCapture(self.camera_index)
        if not self._capture.isOpened():
            self._last_error = "Unable to open camera. Verify camera index and permissions."
            return

        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
        self._capture.set(cv2.CAP_PROP_FPS, CAMERA_FPS)

        self._running = True
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        if self._capture is not None:
            self._capture.release()

    def _reader_loop(self):
        while self._running and self._capture is not None:
            ok, frame = self._capture.read()
            if not ok:
                self._last_error = "Camera read failed."
                time.sleep(0.05)
                continue

            self._last_error = None
            with self._lock:
                self._frame = frame

            time.sleep(0.001)

    def get_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            if self._frame is None:
                return None
            return self._frame.copy()

    def state(self):
        frame = self.get_frame()
        return {
            "running": self._running,
            "ready": frame is not None,
            "last_error": self._last_error,
            "resolution": {
                "width": int(frame.shape[1]) if frame is not None else None,
                "height": int(frame.shape[0]) if frame is not None else None,
            },
        }


class JoystickCommand(BaseModel):
    x: float = Field(..., ge=-1.0, le=1.0)
    y: float = Field(..., ge=-1.0, le=1.0)


class AngleCommand(BaseModel):
    pan: float = Field(..., ge=0.0, le=180.0)
    tilt: float = Field(..., ge=0.0, le=180.0)


controller = GunController(GunConfig())
camera_stream = CameraStream(CAMERA_INDEX)
rate_limiter = SimpleRateLimiter(RATE_LIMIT_PER_MIN)
audit_logger = AuditLogger("audit_logs.db")


flask_app = Flask(__name__, template_folder="templates", static_folder="static")


@flask_app.route("/")
def index():
    return render_template("index.html")


@flask_app.route("/self-test")
def self_test_page():
    return render_template("self_test.html")


@flask_app.route("/video_feed")
def video_feed():
    def generate() -> Generator[bytes, None, None]:
        while True:
            frame = camera_stream.get_frame()
            if frame is None:
                waiting = np.zeros((CAMERA_HEIGHT, CAMERA_WIDTH, 3), dtype=np.uint8)
                cv2.putText(
                    waiting,
                    "Waiting for camera...",
                    (40, 80),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1,
                    (255, 255, 255),
                    2,
                )
                frame = waiting

            success, encoded = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY],
            )
            if not success:
                continue

            jpg = encoded.tobytes()
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            )
            time.sleep(1.0 / max(CAMERA_FPS, 1))

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


api = FastAPI(title="Gun Control API", version="1.0.0")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _role_from_key(api_key: str) -> Optional[str]:
    if not api_key:
        return None
    if OPERATOR_KEY and api_key == OPERATOR_KEY:
        return "operator"
    if OBSERVER_KEY and api_key == OBSERVER_KEY:
        return "observer"
    return None


def _require_roles(request: Request, allowed: Set[str]):
    role = getattr(request.state, "role", "anonymous")
    if role not in allowed:
        raise HTTPException(status_code=403, detail="Forbidden for current role")


def _request_role(request: Request) -> str:
    return getattr(request.state, "role", "anonymous")


@api.middleware("http")
async def security_middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api"):
        return await call_next(request)

    public_paths = {"/api/health", "/api/public-config"}
    request.state.role = "anonymous"

    supplied_key = request.headers.get("X-API-Key", "")
    if AUTH_REQUIRED:
        resolved = _role_from_key(supplied_key)
        if resolved:
            request.state.role = resolved
    elif supplied_key:
        # If auth disabled but key was supplied, treat as operator for convenience.
        request.state.role = "operator"

    if path not in public_paths:
        client_ip = _client_ip(request)
        allowed, retry_after = rate_limiter.check(client_ip)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded."},
                headers={"Retry-After": str(retry_after)},
            )

        if AUTH_REQUIRED:
            if request.state.role == "anonymous":
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        elif request.state.role == "anonymous":
            request.state.role = "operator"

        route_key = (request.method.upper(), path)
        allowed_roles = ROLE_POLICIES.get(route_key)
        if allowed_roles and request.state.role not in allowed_roles:
            return JSONResponse(status_code=403, content={"detail": "Forbidden for current role"})

    return await call_next(request)


@api.on_event("startup")
def on_startup():
    controller.connect()
    controller.start()
    camera_stream.start()


@api.on_event("shutdown")
def on_shutdown():
    camera_stream.stop()
    controller.stop()


@api.get("/api/health")
def health():
    return {
        "ok": True,
        "auth_required": AUTH_REQUIRED,
        "rate_limit_per_min": RATE_LIMIT_PER_MIN,
        "roles_enabled": {
            "operator": bool(OPERATOR_KEY),
            "observer": bool(OBSERVER_KEY),
        },
        "controller": controller.state(),
        "camera": camera_stream.state(),
    }


@api.get("/api/public-config")
def public_config():
    return {
        "auth_required": AUTH_REQUIRED,
        "rate_limit_per_min": RATE_LIMIT_PER_MIN,
        "roles": {
            "operator_enabled": bool(OPERATOR_KEY),
            "observer_enabled": bool(OBSERVER_KEY),
        },
    }


@api.get("/api/state")
def state(request: Request):
    _require_roles(request, {"operator", "observer"})
    state_payload = controller.state()
    state_payload["camera"] = camera_stream.state()
    state_payload["role"] = _request_role(request)
    return state_payload


@api.post("/api/aim")
def aim(command: JoystickCommand, request: Request):
    _require_roles(request, {"operator"})
    controller.set_from_joystick(command.x, command.y)
    audit_logger.log_event(
        role=_request_role(request),
        client_ip=_client_ip(request),
        command="aim",
        payload={"x": command.x, "y": command.y},
        result="ok",
    )
    return controller.state()


@api.post("/api/angles")
def set_angles(command: AngleCommand, request: Request):
    _require_roles(request, {"operator"})
    tilt_clamped = max(controller.config.tilt_min, min(controller.config.tilt_max, command.tilt))
    if tilt_clamped != command.tilt:
        raise HTTPException(
            status_code=422,
            detail=f"Tilt must stay within {controller.config.tilt_min}-{controller.config.tilt_max}.",
        )
    controller.set_target_angles(command.pan, command.tilt)
    audit_logger.log_event(
        role=_request_role(request),
        client_ip=_client_ip(request),
        command="angles",
        payload={"pan": command.pan, "tilt": command.tilt},
        result="ok",
    )
    return controller.state()


@api.post("/api/fire")
def fire(request: Request):
    _require_roles(request, {"operator"})
    controller.fire()
    audit_logger.log_event(
        role=_request_role(request),
        client_ip=_client_ip(request),
        command="fire",
        payload={},
        result="ok",
    )
    return controller.state()


@api.post("/api/center")
def center(request: Request):
    _require_roles(request, {"operator"})
    controller.center()
    audit_logger.log_event(
        role=_request_role(request),
        client_ip=_client_ip(request),
        command="center",
        payload={},
        result="ok",
    )
    return controller.state()


@api.post("/api/self-test")
def self_test(request: Request):
    _require_roles(request, {"operator"})
    result = controller.self_test()
    result["camera"] = camera_stream.state()
    result["timestamp"] = time.time()
    audit_logger.log_event(
        role=_request_role(request),
        client_ip=_client_ip(request),
        command="self-test",
        payload={},
        result="ok" if result.get("ok") else "check",
    )
    return result


@api.get("/api/audit")
def audit(request: Request, limit: int = Query(default=100, ge=1, le=500)):
    _require_roles(request, {"operator"})
    return {
        "role": _request_role(request),
        "events": audit_logger.recent_events(limit=limit),
    }


api.mount("/", WSGIMiddleware(flask_app))


def _cleanup():
    camera_stream.stop()
    controller.stop()


atexit.register(_cleanup)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web_control_server:api", host="0.0.0.0", port=8000, reload=False)
