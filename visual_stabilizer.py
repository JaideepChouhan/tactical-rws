"""
Visual Stabilizer — NCC-Based Gun Stabilization System
=======================================================
Uses Normalized Cross-Correlation (NCC) visual odometry to keep a mounted
gun pointing at the same world point regardless of body/camera movement.

Serial Protocol (Arduino UNO):
    Format:  "PAN,TILT,TRIGGER\\n"
    Pan:     0-180°   (servo on pin 5)
    Tilt:    70-110°  (servo on pin 6)
    Trigger: 45° safe / 135° fire (servo on pin 9)
    Baud:    9600

Controls:
    r       — Reset / re-lock target at current center
    f       — Fire trigger
    +/-     — Increase / decrease template size
    q / ESC — Quit

Author: Built by GitHub Copilot (Claude Opus 4.6) for the AIR0214 RWS project.
"""

import cv2
import numpy as np
import serial
import serial.tools.list_ports
import time
import threading
import sys
import signal

# ──────────────────────────── Configuration ────────────────────────────

SERIAL_PORT = None
BAUDRATE = 9600

# Camera
CAMERA_INDEX = 0
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720

# Template (NCC patch) — size in pixels
TEMPLATE_SIZE = 128          # square patch side length
MIN_TEMPLATE_SIZE = 64
MAX_TEMPLATE_SIZE = 256

# Field of view of the webcam (degrees) — used to map pixels → servo degrees
# Typical laptop webcam is ~60° horizontal, ~45° vertical
CAMERA_HFOV = 60.0
CAMERA_VFOV = 45.0

# Servo limits
PAN_MIN, PAN_MAX = 0, 180
TILT_MIN, TILT_MAX = 70, 110
PAN_CENTER = 90
TILT_CENTER = 90

# Trigger angles
TRIGGER_SAFE = 45
TRIGGER_FIRE = 135

# Smoothing factor (0–1, lower = smoother but more latency)
SMOOTHING = 0.25

# NCC confidence threshold — below this the match is unreliable
NCC_THRESHOLD = 0.4

# Template refresh interval (frames) — refreshes template periodically to
# prevent drift accumulation; 0 = never auto-refresh
TEMPLATE_REFRESH_INTERVAL = 300

# ──────────────────────────── Globals ──────────────────────────────────

arduino = None
current_pan = float(PAN_CENTER)
current_tilt = float(TILT_CENTER)
target_pan = float(PAN_CENTER)
target_tilt = float(TILT_CENTER)
running = True


# ──────────────────────────── Utilities ─────────────────────────────────

def list_serial_ports():
    """List available serial ports."""
    ports = serial.tools.list_ports.comports()
    return [p.device for p in ports]


def connect_arduino(port, baud):
    """Open serial connection to Arduino with retry."""
    if port is None:
        available = [
            p for p in list_serial_ports()
            if any(token in p for token in ('ttyUSB', 'ttyACM', 'usbmodem', 'usbserial', 'cu.usb', 'COM'))
        ]
        preferred = ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyACM0', '/dev/ttyACM1']
        for candidate in preferred:
            if candidate in available:
                port = candidate
                break
        if port is None and available:
            port = available[0]

    if port is None:
        print("[ERROR] No serial ports detected.")
        return None

    try:
        ser = serial.Serial(port=port, baudrate=baud, timeout=1)
        time.sleep(2)  # wait for Arduino reset
        return ser
    except serial.SerialException as e:
        print(f"[ERROR] Could not open {port}: {e}")
        available = list_serial_ports()
        if available:
            print(f"        Available ports: {available}")
        else:
            print("        No serial ports detected.")
        return None


def clamp(value, lo, hi):
    """Clamp a value between lo and hi."""
    return max(lo, min(hi, value))


def pixels_to_degrees(dx_px, dy_px, frame_w, frame_h):
    """Convert pixel displacement to angular displacement in degrees."""
    deg_per_px_h = CAMERA_HFOV / frame_w
    deg_per_px_v = CAMERA_VFOV / frame_h
    return dx_px * deg_per_px_h, dy_px * deg_per_px_v


# ──────────────── NCC Template Tracker ──────────────────────────────────

class NCCTracker:
    """
    Tracks a template patch across frames using Normalized Cross-Correlation.

    On each update():
      1. Convert frame to grayscale
      2. Run cv2.matchTemplate with TM_CCORR_NORMED
      3. Find best-match location
      4. Compute pixel displacement from the original lock position
      5. Return (dx, dy, confidence)
    """

    def __init__(self, template_size=TEMPLATE_SIZE):
        self.template = None
        self.template_gray = None
        self.lock_x = 0          # center-x where template was captured
        self.lock_y = 0          # center-y where template was captured
        self.half = template_size // 2
        self.template_size = template_size
        self.frame_count = 0
        self.locked = False
        # Accumulated offset (degrees) carried across template refreshes
        self.accum_pan_deg = 0.0
        self.accum_tilt_deg = 0.0

    def lock_target(self, frame, cx=None, cy=None, reset_accum=True):
        """
        Capture a template patch from the frame centered at (cx, cy).
        If cx/cy are None, uses the frame center.
        If reset_accum is False, accumulated offsets are preserved (for auto-refresh).
        """
        h, w = frame.shape[:2]
        if cx is None:
            cx = w // 2
        if cy is None:
            cy = h // 2

        half = self.template_size // 2

        # Ensure the ROI is within bounds
        x1 = clamp(cx - half, 0, w - self.template_size)
        y1 = clamp(cy - half, 0, h - self.template_size)
        x2 = x1 + self.template_size
        y2 = y1 + self.template_size

        self.template = frame[y1:y2, x1:x2].copy()
        self.template_gray = cv2.cvtColor(self.template, cv2.COLOR_BGR2GRAY).astype(np.float32)
        self.lock_x = x1 + half
        self.lock_y = y1 + half
        self.half = half
        self.frame_count = 0
        self.locked = True
        if reset_accum:
            self.accum_pan_deg = 0.0
            self.accum_tilt_deg = 0.0
        return True

    def update(self, frame):
        """
        Match the stored template against the current frame.

        Returns:
            (dx, dy, confidence)
            dx, dy: pixel displacement from the lock point (positive = template moved right/down)
            confidence: NCC score 0–1
        """
        if not self.locked or self.template_gray is None:
            return 0.0, 0.0, 0.0

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)

        # Define a search region larger than the template to limit computation
        h, w = gray.shape
        search_margin = self.template_size  # search ±template_size around lock point
        sx1 = clamp(self.lock_x - self.half - search_margin, 0, w - 1)
        sy1 = clamp(self.lock_y - self.half - search_margin, 0, h - 1)
        sx2 = clamp(self.lock_x + self.half + search_margin, 1, w)
        sy2 = clamp(self.lock_y + self.half + search_margin, 1, h)

        search_region = gray[sy1:sy2, sx1:sx2]

        # Ensure search region is larger than template
        if (search_region.shape[0] <= self.template_gray.shape[0] or
                search_region.shape[1] <= self.template_gray.shape[1]):
            # Fallback to full frame
            search_region = gray
            sx1, sy1 = 0, 0

        # Normalized Cross-Correlation
        result = cv2.matchTemplate(search_region, self.template_gray, cv2.TM_CCORR_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

        # best match top-left in search_region coordinates
        match_x = max_loc[0] + sx1 + self.half
        match_y = max_loc[1] + sy1 + self.half

        dx = match_x - self.lock_x
        dy = match_y - self.lock_y
        confidence = max_val

        self.frame_count += 1

        # Auto-refresh template to combat drift; accumulate offset so servos
        # don't snap back to center
        if (TEMPLATE_REFRESH_INTERVAL > 0 and
                self.frame_count >= TEMPLATE_REFRESH_INTERVAL and
                confidence > 0.7):
            # Convert current pixel displacement to degrees and accumulate
            d_pan, d_tilt = pixels_to_degrees(dx, dy, frame.shape[1], frame.shape[0])
            self.accum_pan_deg += d_pan
            self.accum_tilt_deg += d_tilt
            self.lock_target(frame, match_x, match_y, reset_accum=False)

        return float(dx), float(dy), float(confidence)

    def resize_template(self, delta):
        """Resize template by delta pixels (will re-lock on next call)."""
        self.template_size = clamp(self.template_size + delta, MIN_TEMPLATE_SIZE, MAX_TEMPLATE_SIZE)
        self.half = self.template_size // 2
        self.locked = False
        return self.template_size


# ──────────────── Serial Sender Thread ──────────────────────────────────

def serial_sender():
    """
    Background thread that continuously sends smoothed servo positions
    to the Arduino at a fixed rate.
    """
    global current_pan, current_tilt, target_pan, target_tilt, arduino, running

    while running:
        if arduino is None or not arduino.is_open:
            time.sleep(0.1)
            continue

        # Low-pass filter
        current_pan += (target_pan - current_pan) * SMOOTHING
        current_tilt += (target_tilt - current_tilt) * SMOOTHING

        pan_int = int(clamp(current_pan, PAN_MIN, PAN_MAX))
        tilt_int = int(clamp(current_tilt, TILT_MIN, TILT_MAX))

        data = f"{pan_int},{tilt_int},{TRIGGER_SAFE}\n"
        try:
            arduino.write(data.encode())
        except serial.SerialException:
            pass

        time.sleep(0.02)  # ~50 Hz


def fire_trigger():
    """Execute trigger sequence: safe → fire → safe."""
    global arduino, current_pan, current_tilt
    if arduino is None or not arduino.is_open:
        print("[WARN] Arduino not connected — cannot fire.")
        return

    pan_int = int(clamp(current_pan, PAN_MIN, PAN_MAX))
    tilt_int = int(clamp(current_tilt, TILT_MIN, TILT_MAX))

    try:
        arduino.write(f"{pan_int},{tilt_int},{TRIGGER_FIRE}\n".encode())
        print("🔥 FIRE!")
        time.sleep(0.2)
        arduino.write(f"{pan_int},{tilt_int},{TRIGGER_SAFE}\n".encode())
        print("⬅️  Trigger safe")
    except serial.SerialException as e:
        print(f"[ERROR] Trigger failed: {e}")


# ──────────────── HUD Drawing ──────────────────────────────────────────

def draw_hud(frame, tracker, dx, dy, confidence, fps):
    """Draw heads-up display overlay on the frame."""
    h, w = frame.shape[:2]
    cx, cy = w // 2, h // 2

    # Crosshair at center
    cv2.line(frame, (cx - 20, cy), (cx + 20, cy), (0, 255, 0), 1)
    cv2.line(frame, (cx, cy - 20), (cx, cy + 20), (0, 255, 0), 1)
    cv2.circle(frame, (cx, cy), 5, (0, 255, 0), 1)

    if tracker.locked:
        half = tracker.half
        # Draw template region (where we locked)
        cv2.rectangle(frame,
                       (tracker.lock_x - half, tracker.lock_y - half),
                       (tracker.lock_x + half, tracker.lock_y + half),
                       (255, 255, 0), 1)

        # Draw where the match was found (displaced position)
        match_cx = int(tracker.lock_x + dx)
        match_cy = int(tracker.lock_y + dy)
        cv2.rectangle(frame,
                       (match_cx - half, match_cy - half),
                       (match_cx + half, match_cy + half),
                       (0, 0, 255), 2)

        # Displacement vector
        cv2.arrowedLine(frame,
                         (tracker.lock_x, tracker.lock_y),
                         (match_cx, match_cy),
                         (0, 255, 255), 2, tipLength=0.3)

        # Confidence color: green if good, red if bad
        conf_color = (0, 255, 0) if confidence > NCC_THRESHOLD else (0, 0, 255)
        status = "LOCKED" if confidence > NCC_THRESHOLD else "LOW CONFIDENCE"
    else:
        conf_color = (128, 128, 128)
        status = "UNLOCKED — press 'r' to lock"
        confidence = 0.0

    # Info panel
    panel_y = 20
    cv2.putText(frame, f"Status: {status}", (10, panel_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, conf_color, 1)
    panel_y += 22
    cv2.putText(frame, f"NCC: {confidence:.3f}", (10, panel_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, conf_color, 1)
    panel_y += 22
    cv2.putText(frame, f"Disp: dx={dx:+.1f}px  dy={dy:+.1f}px", (10, panel_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    panel_y += 22
    cv2.putText(frame, f"Pan: {int(current_pan)}  Tilt: {int(current_tilt)}", (10, panel_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    panel_y += 22
    cv2.putText(frame, f"Template: {tracker.template_size}px", (10, panel_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    panel_y += 22
    cv2.putText(frame, f"FPS: {fps:.1f}", (10, panel_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    # Controls legend (bottom)
    legend_y = h - 10
    cv2.putText(frame, "r:Lock  f:Fire  +/-:Template  q:Quit", (10, legend_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 180, 180), 1)

    return frame


# ──────────────── Main Loop ─────────────────────────────────────────────

def main():
    global arduino, target_pan, target_tilt, running

    print("=" * 60)
    print("  Visual Stabilizer — NCC-Based Gun Stabilization")
    print("=" * 60)

    # ── Connect Arduino ──
    display_port = SERIAL_PORT if SERIAL_PORT is not None else "auto-detect"
    print(f"\n[INFO] Attempting serial connection on {display_port} @ {BAUDRATE}...")
    arduino = connect_arduino(SERIAL_PORT, BAUDRATE)
    if arduino:
        print(f"[OK]   Connected to {SERIAL_PORT}")
    else:
        print("[WARN] Running in PREVIEW mode (no Arduino connected)")

    # ── Open Camera ──
    print(f"[INFO] Opening camera index {CAMERA_INDEX}...")
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("[ERROR] Cannot open camera. Check camera index or permissions.")
        if arduino and arduino.is_open:
            arduino.close()
        sys.exit(1)

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, 30)
    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"[OK]   Camera opened at {actual_w}x{actual_h}")

    cv2.namedWindow("Visual Stabilizer — NCC Gun Control", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Visual Stabilizer — NCC Gun Control", actual_w, actual_h)

    # ── Start serial sender thread ──
    sender = threading.Thread(target=serial_sender, daemon=True)
    sender.start()

    # ── Tracker ──
    tracker = NCCTracker(TEMPLATE_SIZE)

    print("\n[INFO] Controls:")
    print("         r   — Lock/reset target at frame center")
    print("         f   — Fire trigger")
    print("        +/-  — Resize template patch")
    print("        q/ESC — Quit")
    print("-" * 60)

    fps = 0.0
    prev_time = time.time()

    try:
        while running:
            ret, frame = cap.read()
            if not ret:
                print("[WARN] Frame grab failed, retrying...")
                time.sleep(0.05)
                continue

            # ── NCC tracking ──
            dx, dy, confidence = 0.0, 0.0, 0.0
            if tracker.locked:
                dx, dy, confidence = tracker.update(frame)

                if confidence > NCC_THRESHOLD:
                    # Convert current frame displacement to degrees
                    d_pan_deg, d_tilt_deg = pixels_to_degrees(dx, dy, actual_w, actual_h)

                    # Total angular offset = accumulated (from past refreshes) + current
                    total_pan_deg = tracker.accum_pan_deg + d_pan_deg
                    total_tilt_deg = tracker.accum_tilt_deg + d_tilt_deg

                    # Compensation: when scene shifts left (body turned right),
                    # dx < 0, so we ADD to pan to move servo left (counter-rotate).
                    # When scene shifts down (body tilted up), dy > 0, so we
                    # SUBTRACT from tilt to tilt servo down (counter-tilt).
                    target_pan = clamp(PAN_CENTER + total_pan_deg, PAN_MIN, PAN_MAX)
                    target_tilt = clamp(TILT_CENTER - total_tilt_deg, TILT_MIN, TILT_MAX)

            # ── FPS ──
            now = time.time()
            dt = now - prev_time
            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1.0 / dt)
            prev_time = now

            # ── Draw HUD ──
            display = draw_hud(frame, tracker, dx, dy, confidence, fps)
            cv2.imshow("Visual Stabilizer — NCC Gun Control", display)

            # ── Keyboard ──
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q') or key == 27:  # q or ESC
                break
            elif key == ord('r'):
                tracker.lock_target(frame)
                target_pan = float(PAN_CENTER)
                target_tilt = float(TILT_CENTER)
                print("[INFO] Target LOCKED at frame center")
            elif key == ord('f'):
                threading.Thread(target=fire_trigger, daemon=True).start()
            elif key == ord('+') or key == ord('='):
                new_size = tracker.resize_template(16)
                print(f"[INFO] Template size → {new_size}px")
            elif key == ord('-') or key == ord('_'):
                new_size = tracker.resize_template(-16)
                print(f"[INFO] Template size → {new_size}px")

    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user")
    finally:
        running = False
        print("[INFO] Shutting down...")

        # Return servos to center
        if arduino and arduino.is_open:
            try:
                arduino.write(f"{PAN_CENTER},{TILT_CENTER},{TRIGGER_SAFE}\n".encode())
                time.sleep(0.3)
                arduino.close()
                print("[OK]   Servos centered, serial closed")
            except serial.SerialException:
                pass

        cap.release()
        cv2.destroyAllWindows()
        print("[OK]   Camera released. Goodbye!")


# ──────────────── Entry Point ───────────────────────────────────────────

if __name__ == "__main__":
    # Graceful Ctrl+C
    signal.signal(signal.SIGINT, lambda *_: setattr(sys.modules[__name__], 'running', False))
    main()
