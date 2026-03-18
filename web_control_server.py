from __future__ import annotations

import atexit
import os
import threading
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, Generator, Literal, Optional, Set, Tuple

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.wsgi import WSGIMiddleware
from fastapi.responses import JSONResponse
from flask import Flask, Response, render_template
from pydantic import BaseModel, Field

from audit_logger import AuditLogger
from gun_controller import GunConfig, GunController


CAMERA_INDEX = 0
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720
CAMERA_FPS = 30
JPEG_QUALITY = 85
DETECT_WIDTH = 480
FACE_DETECT_INTERVAL = 2
MAX_API_BODY_BYTES = int(os.getenv("RWS_MAX_API_BODY_BYTES", "32768"))
IDEMPOTENCY_TTL_SECONDS = int(os.getenv("RWS_IDEMPOTENCY_TTL_SECONDS", "30"))
FIRE_COOLDOWN_SECONDS = float(os.getenv("RWS_FIRE_COOLDOWN_SECONDS", "0.45"))

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
    ("GET", "/api/target-lock/state"): {"operator", "observer"},
    ("POST", "/api/target-lock/config"): {"operator"},
    ("POST", "/api/target-lock/enable"): {"operator"},
    ("POST", "/api/target-lock/disable"): {"operator"},
    ("POST", "/api/target-lock/manual-target"): {"operator"},
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


class FireGuard:
    def __init__(self, cooldown_seconds: float, ttl_seconds: int):
        self.cooldown_seconds = max(0.1, cooldown_seconds)
        self.ttl_seconds = max(1, ttl_seconds)
        self._lock = threading.Lock()
        self._last_fired_at = 0.0
        self._cache: Dict[str, Dict[str, Any]] = {}

    def _prune(self):
        now = time.time()
        stale = [k for k, v in self._cache.items() if now > v["expires_at"]]
        for key in stale:
            del self._cache[key]

    def check_and_get_cached(self, idempotency_key: Optional[str]) -> Optional[Dict[str, Any]]:
        if not idempotency_key:
            return None
        with self._lock:
            self._prune()
            cached = self._cache.get(idempotency_key)
            if not cached:
                return None
            return cached.get("payload")

    def store_cached(self, idempotency_key: Optional[str], payload: Dict[str, Any]):
        if not idempotency_key:
            return
        with self._lock:
            self._prune()
            self._cache[idempotency_key] = {
                "payload": payload,
                "expires_at": time.time() + self.ttl_seconds,
            }

    def check_cooldown(self) -> bool:
        with self._lock:
            now = time.time()
            if (now - self._last_fired_at) < self.cooldown_seconds:
                return False
            self._last_fired_at = now
            return True


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


class TargetLockManager:
    def __init__(self, camera: CameraStream, gun: GunController):
        self.camera = camera
        self.gun = gun
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self.enabled = False
        self.mode: Literal["face", "object", "manual"] = "face"
        self.deadzone = 0.06
        self.kp_pan = 2.3
        self.kp_tilt = 2.0
        self.max_step = 2.1
        self.manual_response = 0.38
        self.auto_response = 0.42

        self.locked = False
        self.confidence = 0.0
        self.target_center: Optional[Tuple[int, int]] = None
        self.target_bbox: Optional[Tuple[int, int, int, int]] = None
        self.manual_target_norm: Tuple[float, float] = (0.5, 0.5)
        self.manual_target_filtered: Tuple[float, float] = (0.5, 0.5)
        self.auto_target_filtered: Tuple[float, float] = (0.5, 0.5)
        self.last_detection_ts: Optional[float] = None
        self.missed_frames = 0
        self._frame_counter = 0
        self._last_face_detection: Optional[Tuple[int, int, int, int, float]] = None

        cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        self.face_cascade = cv2.CascadeClassifier(cascade_path)
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=36)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def configure(
        self,
        *,
        enabled: Optional[bool] = None,
        mode: Optional[Literal["face", "object", "manual"]] = None,
        deadzone: Optional[float] = None,
        kp_pan: Optional[float] = None,
        kp_tilt: Optional[float] = None,
        max_step: Optional[float] = None,
        manual_response: Optional[float] = None,
        auto_response: Optional[float] = None,
    ):
        with self._lock:
            if enabled is not None:
                self.enabled = enabled
                if not enabled:
                    self.locked = False
            if mode is not None:
                self.mode = mode
                self.locked = False
            if deadzone is not None:
                self.deadzone = float(max(0.01, min(deadzone, 0.35)))
            if kp_pan is not None:
                self.kp_pan = float(max(0.1, min(kp_pan, 10.0)))
            if kp_tilt is not None:
                self.kp_tilt = float(max(0.1, min(kp_tilt, 10.0)))
            if max_step is not None:
                self.max_step = float(max(0.2, min(max_step, 10.0)))
            if manual_response is not None:
                self.manual_response = float(max(0.05, min(manual_response, 1.0)))
            if auto_response is not None:
                self.auto_response = float(max(0.05, min(auto_response, 1.0)))

    def set_manual_target(self, x_norm: float, y_norm: float):
        with self._lock:
            self.manual_target_norm = (
                float(max(0.0, min(1.0, x_norm))),
                float(max(0.0, min(1.0, y_norm))),
            )
            if self.mode == "manual":
                self.locked = self.enabled

    @staticmethod
    def _map_value(x: float, in_min: float, in_max: float, out_min: float, out_max: float) -> float:
        if in_max == in_min:
            return out_min
        return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min

    def _manual_target_to_angles(self) -> Tuple[float, float]:
        cfg = self.gun.config
        mx, my = self.manual_target_filtered
        target_pan = self._map_value(mx, 0.0, 1.0, cfg.pan_min, cfg.pan_max)
        target_tilt = self._map_value(my, 0.0, 1.0, cfg.tilt_max, cfg.tilt_min)
        return float(target_pan), float(target_tilt)

    def _norm_to_angles(self, nx: float, ny: float) -> Tuple[float, float]:
        cfg = self.gun.config
        target_pan = self._map_value(nx, 0.0, 1.0, cfg.pan_min, cfg.pan_max)
        target_tilt = self._map_value(ny, 0.0, 1.0, cfg.tilt_max, cfg.tilt_min)
        return float(target_pan), float(target_tilt)

    def _detect_face(self, frame: np.ndarray) -> Optional[Tuple[int, int, int, int, float]]:
        if self.face_cascade.empty():
            return None
        h, w = frame.shape[:2]
        scale = 1.0
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if w > DETECT_WIDTH:
            scale = DETECT_WIDTH / float(w)
            scaled_h = max(1, int(h * scale))
            gray = cv2.resize(gray, (DETECT_WIDTH, scaled_h), interpolation=cv2.INTER_LINEAR)

        self._frame_counter += 1
        if (self._frame_counter % FACE_DETECT_INTERVAL) != 0 and self._last_face_detection is not None:
            return self._last_face_detection

        faces = self.face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(32, 32))
        if len(faces) == 0:
            self._last_face_detection = None
            return None

        if scale != 1.0:
            inv = 1.0 / scale
            faces = [(int(x * inv), int(y * inv), int(fw * inv), int(fh * inv)) for (x, y, fw, fh) in faces]

        frame_center = (w // 2, h // 2)
        best = None
        best_score = -1.0
        for (x, y, fw, fh) in faces:
            cx = x + fw // 2
            cy = y + fh // 2
            dist = np.hypot(cx - frame_center[0], cy - frame_center[1])
            area = fw * fh
            score = area / max(1.0, dist + 1.0)
            if score > best_score:
                best_score = score
                best = (x, y, fw, fh)

        if not best:
            return None

        x, y, fw, fh = best
        confidence = min(1.0, max(0.2, (fw * fh) / float(w * h * 0.2)))
        self._last_face_detection = (int(x), int(y), int(fw), int(fh), float(confidence))
        return self._last_face_detection

    def _detect_object(self, frame: np.ndarray) -> Optional[Tuple[int, int, int, int, float]]:
        h, w = frame.shape[:2]
        scale = 1.0
        proc = frame
        if w > DETECT_WIDTH:
            scale = DETECT_WIDTH / float(w)
            scaled_h = max(1, int(h * scale))
            proc = cv2.resize(frame, (DETECT_WIDTH, scaled_h), interpolation=cv2.INTER_LINEAR)

        fg = self.bg_subtractor.apply(proc)
        _, mask = cv2.threshold(fg, 215, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_DILATE, np.ones((5, 5), np.uint8))

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        proc_h, proc_w = proc.shape[:2]
        frame_center = (proc_w // 2, proc_h // 2)
        best = None
        best_score = -1.0
        min_area = (proc_w * proc_h) * 0.002

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < min_area:
                continue
            x, y, bw, bh = cv2.boundingRect(contour)
            cx = x + bw // 2
            cy = y + bh // 2
            dist = np.hypot(cx - frame_center[0], cy - frame_center[1])
            score = area / max(1.0, dist + 1.0)
            if score > best_score:
                best_score = score
                best = (
                    int(x),
                    int(y),
                    int(bw),
                    int(bh),
                    float(min(1.0, area / float(proc_w * proc_h * 0.22))),
                )

        if best is not None and scale != 1.0:
            x, y, bw, bh, conf = best
            inv = 1.0 / scale
            best = (int(x * inv), int(y * inv), int(bw * inv), int(bh * inv), float(conf))

        return best

    def _detect_manual(self, frame: np.ndarray) -> Tuple[int, int, int, int, float]:
        h, w = frame.shape[:2]
        mx, my = self.manual_target_norm
        center_x = int(mx * (w - 1))
        center_y = int(my * (h - 1))
        box_w = max(20, int(w * 0.05))
        box_h = max(20, int(h * 0.05))
        x = int(max(0, min(w - box_w, center_x - box_w // 2)))
        y = int(max(0, min(h - box_h, center_y - box_h // 2)))
        return x, y, box_w, box_h, 1.0

    def _detect_target(self, frame: np.ndarray) -> Optional[Tuple[int, int, int, int, float]]:
        if self.mode == "manual":
            return self._detect_manual(frame)
        if self.mode == "face":
            return self._detect_face(frame)
        return self._detect_object(frame)

    def _loop(self):
        while self._running:
            frame = self.camera.get_frame()
            if frame is None:
                time.sleep(0.05)
                continue

            detection = self._detect_target(frame)
            h, w = frame.shape[:2]
            center_x = w // 2
            center_y = h // 2

            with self._lock:
                if self.mode == "manual":
                    fx, fy = self.manual_target_filtered
                    tx, ty = self.manual_target_norm
                    alpha = self.manual_response
                    fx += (tx - fx) * alpha
                    fy += (ty - fy) * alpha
                    self.manual_target_filtered = (float(fx), float(fy))

                    x, y, bw, bh, confidence = self._detect_manual(frame)
                    target_x = x + bw // 2
                    target_y = y + bh // 2
                    self.target_bbox = (int(x), int(y), int(bw), int(bh))
                    self.target_center = (int(target_x), int(target_y))
                    self.confidence = float(confidence)
                    self.last_detection_ts = time.time()

                    if self.enabled:
                        pan_target, tilt_target = self._manual_target_to_angles()
                        self.gun.set_target_angles(pan_target, tilt_target)
                        self.locked = True
                    else:
                        self.locked = False
                    manual_handled = True
                else:
                    manual_handled = False

                if manual_handled:
                    pass
                elif detection is None:
                    self.missed_frames += 1
                    if self.missed_frames > 6:
                        self.locked = False
                        self.target_bbox = None
                        self.target_center = None
                        self.confidence = 0.0
                    should_continue = True
                else:
                    should_continue = False
                if not manual_handled and not should_continue:
                    self.missed_frames = 0
                    x, y, bw, bh, confidence = detection
                    target_x = x + bw // 2
                    target_y = y + bh // 2
                    self.target_bbox = (int(x), int(y), int(bw), int(bh))
                    self.target_center = (int(target_x), int(target_y))
                    self.confidence = float(confidence)
                    self.last_detection_ts = time.time()

                    if self.enabled:
                        nx = float(max(0.0, min(1.0, target_x / max(1.0, w - 1))))
                        ny = float(max(0.0, min(1.0, target_y / max(1.0, h - 1))))

                        fx, fy = self.auto_target_filtered
                        alpha = self.auto_response
                        fx += (nx - fx) * alpha
                        fy += (ny - fy) * alpha
                        self.auto_target_filtered = (float(fx), float(fy))

                        pan_target, tilt_target = self._norm_to_angles(float(fx), float(fy))
                        self.gun.set_target_angles(pan_target, tilt_target)
                        self.locked = True
                    else:
                        self.locked = False

            if manual_handled:
                time.sleep(0.02)
                continue

            if detection is None:
                time.sleep(0.03)
                continue

            time.sleep(0.03)

    def state(self) -> Dict[str, Any]:
        with self._lock:
            bbox = None
            center = None
            if self.target_bbox is not None:
                bbox = [int(v) for v in self.target_bbox]
            if self.target_center is not None:
                center = [int(v) for v in self.target_center]
            return {
                "enabled": self.enabled,
                "mode": self.mode,
                "locked": self.locked,
                "confidence": round(self.confidence, 3),
                "target_center": center,
                "target_bbox": bbox,
                "manual_target_norm": [
                    round(float(self.manual_target_norm[0]), 4),
                    round(float(self.manual_target_norm[1]), 4),
                ],
                "manual_target_filtered": [
                    round(float(self.manual_target_filtered[0]), 4),
                    round(float(self.manual_target_filtered[1]), 4),
                ],
                "last_detection_ts": self.last_detection_ts,
                "deadzone": self.deadzone,
                "kp_pan": self.kp_pan,
                "kp_tilt": self.kp_tilt,
                "max_step": self.max_step,
                "manual_response": self.manual_response,
                "auto_response": self.auto_response,
            }

    def annotate_frame(self, frame: np.ndarray) -> np.ndarray:
        overlay = frame.copy()
        h, w = overlay.shape[:2]
        cx, cy = w // 2, h // 2
        cv2.line(overlay, (cx - 24, cy), (cx + 24, cy), (0, 250, 0), 2)
        cv2.line(overlay, (cx, cy - 24), (cx, cy + 24), (0, 250, 0), 2)

        with self._lock:
            enabled = self.enabled
            mode = self.mode
            locked = self.locked
            confidence = self.confidence
            bbox = self.target_bbox
            manual_target_norm = self.manual_target_norm
            target_center = self.target_center

        if bbox is not None:
            x, y, bw, bh = bbox
            color = (22, 217, 245) if locked and enabled else (0, 145, 255)
            cv2.rectangle(overlay, (x, y), (x + bw, y + bh), color, 2)

        if mode == "manual":
            mx = int(manual_target_norm[0] * max(1, w - 1))
            my = int(manual_target_norm[1] * max(1, h - 1))
            cv2.circle(overlay, (mx, my), 18, (38, 220, 255), 2)
            cv2.circle(overlay, (mx, my), 6, (38, 220, 255), 1)
            cv2.line(overlay, (mx - 24, my), (mx + 24, my), (38, 220, 255), 1)
            cv2.line(overlay, (mx, my - 24), (mx, my + 24), (38, 220, 255), 1)

        if target_center is not None and enabled:
            tx, ty = int(target_center[0]), int(target_center[1])
            point_color = (24, 247, 110) if locked else (0, 195, 255)
            cv2.circle(overlay, (tx, ty), 14, point_color, 2)
            cv2.circle(overlay, (tx, ty), 4, point_color, -1)
            cv2.line(overlay, (tx - 18, ty), (tx + 18, ty), point_color, 1)
            cv2.line(overlay, (tx, ty - 18), (tx, ty + 18), point_color, 1)

        status = f"AI LOCK {'ON' if enabled else 'OFF'} | MODE {mode.upper()} | CONF {confidence:.2f}"
        status_color = (22, 217, 245) if enabled else (125, 125, 125)
        cv2.putText(
            overlay,
            status,
            (18, 36),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            status_color,
            2,
        )
        return overlay


class JoystickCommand(BaseModel):
    x: float = Field(..., ge=-1.0, le=1.0)
    y: float = Field(..., ge=-1.0, le=1.0)


class AngleCommand(BaseModel):
    pan: float = Field(..., ge=0.0, le=180.0)
    tilt: float = Field(..., ge=0.0, le=180.0)


class TargetLockConfigCommand(BaseModel):
    enabled: Optional[bool] = None
    mode: Optional[Literal["face", "object", "manual"]] = None
    deadzone: Optional[float] = Field(default=None, ge=0.01, le=0.35)
    kp_pan: Optional[float] = Field(default=None, ge=0.1, le=10.0)
    kp_tilt: Optional[float] = Field(default=None, ge=0.1, le=10.0)
    max_step: Optional[float] = Field(default=None, ge=0.2, le=10.0)
    manual_response: Optional[float] = Field(default=None, ge=0.05, le=1.0)
    auto_response: Optional[float] = Field(default=None, ge=0.05, le=1.0)


class ManualTargetCommand(BaseModel):
    x: float = Field(..., ge=0.0, le=1.0)
    y: float = Field(..., ge=0.0, le=1.0)


controller = GunController(GunConfig())
camera_stream = CameraStream(CAMERA_INDEX)
rate_limiter = SimpleRateLimiter(RATE_LIMIT_PER_MIN)
audit_logger = AuditLogger("audit_logs.db")
target_lock = TargetLockManager(camera_stream, controller)
fire_guard = FireGuard(FIRE_COOLDOWN_SECONDS, IDEMPOTENCY_TTL_SECONDS)


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

            frame = target_lock.annotate_frame(frame)

            success, encoded = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY],
            )
            if not success:
                continue

            jpg = encoded.tobytes()
            yield b"--frame\r\n" b"Content-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            time.sleep(1.0 / max(CAMERA_FPS, 1))

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


api = FastAPI(title="Gun Control API", version="1.1.0")


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
async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.2f}"
    return response


@api.middleware("http")
async def payload_guard_middleware(request: Request, call_next):
    if request.url.path.startswith("/api") and request.method.upper() in {"POST", "PUT", "PATCH"}:
        content_length_raw = request.headers.get("content-length", "0")
        try:
            content_length = int(content_length_raw)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length header"})

        if content_length > MAX_API_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Payload too large. Limit is {MAX_API_BODY_BYTES} bytes."},
            )

        if content_length > 0:
            content_type = request.headers.get("content-type", "")
            if "application/json" not in content_type:
                return JSONResponse(status_code=415, content={"detail": "Content-Type must be application/json"})

    return await call_next(request)


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
        request.state.role = "operator"

    if path not in public_paths:
        client_ip = _client_ip(request)
        role = request.state.role
        limiter_key = f"{client_ip}:{role}:{request.method.upper()}:{path}"
        allowed, retry_after = rate_limiter.check(limiter_key)
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
    target_lock.start()


@api.on_event("shutdown")
def on_shutdown():
    target_lock.stop()
    camera_stream.stop()
    controller.stop()


@api.get("/api/health")
def health():
    return {
        "ok": True,
        "auth_required": AUTH_REQUIRED,
        "rate_limit_per_min": RATE_LIMIT_PER_MIN,
        "max_api_body_bytes": MAX_API_BODY_BYTES,
        "roles_enabled": {
            "operator": bool(OPERATOR_KEY),
            "observer": bool(OBSERVER_KEY),
        },
        "controller": controller.state(),
        "camera": camera_stream.state(),
        "target_lock": target_lock.state(),
    }


@api.get("/api/public-config")
def public_config():
    return {
        "auth_required": AUTH_REQUIRED,
        "rate_limit_per_min": RATE_LIMIT_PER_MIN,
        "max_api_body_bytes": MAX_API_BODY_BYTES,
        "idempotency_ttl_seconds": IDEMPOTENCY_TTL_SECONDS,
        "fire_cooldown_seconds": FIRE_COOLDOWN_SECONDS,
        "roles": {
            "operator_enabled": bool(OPERATOR_KEY),
            "observer_enabled": bool(OBSERVER_KEY),
        },
        "target_lock_modes": ["face", "object", "manual"],
        "target_lock_defaults": {
            "deadzone": 0.06,
            "kp_pan": 2.3,
            "kp_tilt": 2.0,
            "max_step": 2.1,
            "manual_response": 0.38,
            "auto_response": 0.42,
        },
    }


@api.get("/api/state")
def state(request: Request):
    _require_roles(request, {"operator", "observer"})
    state_payload = controller.state()
    state_payload["camera"] = camera_stream.state()
    state_payload["target_lock"] = target_lock.state()
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

    idempotency_key = request.headers.get("Idempotency-Key", "").strip() or None
    cached = fire_guard.check_and_get_cached(idempotency_key)
    if cached is not None:
        return cached

    if not fire_guard.check_cooldown():
        raise HTTPException(status_code=429, detail="Fire cooldown active. Retry shortly.")

    controller.fire()
    response_payload = controller.state()

    fire_guard.store_cached(idempotency_key, response_payload)
    audit_logger.log_event(
        role=_request_role(request),
        client_ip=_client_ip(request),
        command="fire",
        payload={"idempotency_key": bool(idempotency_key)},
        result="ok",
    )
    return response_payload


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
    result["target_lock"] = target_lock.state()
    result["timestamp"] = time.time()
    audit_logger.log_event(
        role=_request_role(request),
        client_ip=_client_ip(request),
        command="self-test",
        payload={},
        result="ok" if result.get("ok") else "check",
    )
    return result


@api.get("/api/target-lock/state")
def target_lock_state(request: Request):
    _require_roles(request, {"operator", "observer"})
    return {
        "role": _request_role(request),
        "target_lock": target_lock.state(),
    }


@api.post("/api/target-lock/config")
def target_lock_config(command: TargetLockConfigCommand, request: Request):
    _require_roles(request, {"operator"})
    target_lock.configure(
        enabled=command.enabled,
        mode=command.mode,
        deadzone=command.deadzone,
        kp_pan=command.kp_pan,
        kp_tilt=command.kp_tilt,
        max_step=command.max_step,
        manual_response=command.manual_response,
        auto_response=command.auto_response,
    )
    payload = target_lock.state()
    audit_logger.log_event(
        role=_request_role(request),
        client_ip=_client_ip(request),
        command="target-lock-config",
        payload={
            "enabled": command.enabled,
            "mode": command.mode,
            "deadzone": command.deadzone,
            "kp_pan": command.kp_pan,
            "kp_tilt": command.kp_tilt,
            "max_step": command.max_step,
            "manual_response": command.manual_response,
            "auto_response": command.auto_response,
        },
        result="ok",
    )
    return payload


@api.post("/api/target-lock/manual-target")
def target_lock_manual_target(command: ManualTargetCommand, request: Request):
    _require_roles(request, {"operator"})
    target_lock.set_manual_target(command.x, command.y)
    payload = target_lock.state()
    audit_logger.log_event(
        role=_request_role(request),
        client_ip=_client_ip(request),
        command="target-lock-manual-target",
        payload={"x": command.x, "y": command.y},
        result="ok",
    )
    return payload


@api.post("/api/target-lock/enable")
def target_lock_enable(request: Request):
    _require_roles(request, {"operator"})
    target_lock.configure(enabled=True)
    payload = target_lock.state()
    audit_logger.log_event(
        role=_request_role(request),
        client_ip=_client_ip(request),
        command="target-lock-enable",
        payload={},
        result="ok",
    )
    return payload


@api.post("/api/target-lock/disable")
def target_lock_disable(request: Request):
    _require_roles(request, {"operator"})
    target_lock.configure(enabled=False)
    payload = target_lock.state()
    audit_logger.log_event(
        role=_request_role(request),
        client_ip=_client_ip(request),
        command="target-lock-disable",
        payload={},
        result="ok",
    )
    return payload


@api.get("/api/audit")
def audit(request: Request, limit: int = Query(default=100, ge=1, le=500)):
    _require_roles(request, {"operator"})
    return {
        "role": _request_role(request),
        "events": audit_logger.recent_events(limit=limit),
    }


api.mount("/", WSGIMiddleware(flask_app))


def _cleanup():
    target_lock.stop()
    camera_stream.stop()
    controller.stop()


atexit.register(_cleanup)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web_control_server:api", host="0.0.0.0", port=8000, reload=False)