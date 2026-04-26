# AIR0214 Smart RWS Platform

Remote Weapon Station control stack with:
- secure dual-role API access,
- OpenCV-powered AI target lock,
- live web command console,
- persistent command audit trail,
- startup diagnostics and self-test automation.

---

## System Snapshot

| Area | What it does |
|---|---|
| Live Ops UI | Real-time camera feed, joystick aiming, keyboard control, fire/center commands |
| AI Assist | Face lock, centered object lock, and manual target locking reticle |
| API Security | RBAC, per-route enforcement, rate limiting, request hardening |
| Reliability | Idempotent fire handling, cooldown guard, response-time/request-id tracing |
| Traceability | SQLite audit logs for movement, fire, and lock-control events |

---

## Visual Architecture

```mermaid
flowchart LR
    A[Web Browser Console\nindex.html + app.js] -->|REST + API Key| B[FastAPI Core\nweb_control_server.py]
    A -->|MJPEG Stream| C[Flask /video_feed]
    C --> D[CameraStream\nOpenCV Capture]
    D --> E[TargetLockManager\nFace/Object/Manual Control Loop]
    E --> B
    B --> F[GunController\nPan Tilt Trigger]
    F --> G[Arduino Servo Firmware\ntripod_control.ino]
    B --> H[AuditLogger\nSQLite audit_logs.db]
```

---

## New AI Features Added

### Standalone Tripod Firmware (New)

Added a dedicated standalone Arduino firmware in `tripod_control_standalone/tripod_control_standalone.ino` for field use without requiring continuous host control.

Key capabilities:

- 6-button direct control using internal pull-ups (no external resistors required):
  - UP, DOWN, LEFT, RIGHT, TRIGGER, MODE
- Three operating modes (cycled by MODE button):
  - `MANUAL`: full D-pad pan/tilt control + trigger pulse
  - `PATROL`: automatic left-right pan sweep + manual tilt + trigger
  - `SENTRY`: center-lock behavior with trigger active and optional pan micro-corrections
- Smooth continuous motion model for local buttons:
  - movement is time-based (continuous while held), not jump-to-endpoint
  - supports simultaneous two-button diagonals (LEFT+UP, RIGHT+DOWN, etc.)
  - diagonal speed is normalized for consistent control feel
- Trigger pulse handling in firmware:
  - controlled fire-hold duration, auto-return to safe trigger angle
- Serial compatibility with existing host packet format:
  - continues to accept `PAN,TILT,TRIGGER\n` from laptop/server
  - host commands take priority immediately when packets are present
  - automatic fallback to local button control after serial idle timeout
- Built-in debounce with low-latency continuous movement ticks for responsive local control.

This enables reliable standalone operation while preserving compatibility with the existing web/server control pipeline.

### 1) AI Target Lock (OpenCV)

Three tracking modes are integrated directly into the server and web UI:

- Face mode
  - Uses Haar cascade frontal-face detection.
  - Uses downscaled-frame detection plus short-interval caching for faster response.
  - Picks the strongest candidate near frame center.
  - Converts locked target point directly to absolute pan/tilt mapping.

- Centered object mode
  - Uses foreground segmentation (MOG2) and contour scoring.
  - Runs on a resized processing frame for lower latency.
  - Tracks the dominant moving object nearest the center.
  - Converts locked target point directly to absolute pan/tilt mapping.

- Manual target locking mode
  - Operator drags a reticle circle over the live camera frame.
  - The selected manual point becomes the lock reference.
  - Uses cursor-style absolute mapping from reticle position to pan/tilt angles.
  - Adds filtered manual response for smooth yet fast tracking behavior.

Servo correction behavior:

- Auto modes now use detect-then-absolute-aim logic (manual-style mapping).
- Manual mode uses draggable reticle absolute mapping.
- Both paths use response filters to keep movement quick but smooth.

Live stream overlay now shows:
- center crosshair,
- detected target bounding box,
- gun-point marker on current lock,
- lock mode and confidence status text.

### 2) Web UI Integration

Added operator controls to dashboard:

- mode switch: Face / Centered object / Manual target locking,
- lock enable/disable,
- mode/enable buttons with immediate visual color state cues,
- live deadzone tuning,
- pan gain tuning,
- auto response tuning,
- manual response tuning,
- lock telemetry cards (mode, state, confidence),
- draggable reticle overlay visible in Manual mode,
- tactical terminal-style live text stream under camera window.

### 3) Industry-Grade Responsiveness Plan (Executed)

The following upgrades were planned and implemented:

- Control precision path
  - Manual mode changed from visual error chasing to absolute cursor-style mapping.
  - This removes oscillation and improves point-to-point accuracy.

- Real-time smoothness
  - Face and object lock switched to absolute target-to-angle mapping after detection lock.
  - Added fast detection path with frame resizing and short-interval face cache.
  - Added gun-point marker rendering on locked targets.
  - Added manual response filter in backend loop (configurable).
  - Added auto response filter for face/object modes (configurable).
  - Added non-blocking reticle drag updates so UI does not stall on network RTT.
  - Reduced dashboard state polling interval for near real-time telemetry refresh.

- Operational hardening
  - Maintained request IDs, payload guards, scoped rate limits, and idempotent fire path.
  - Continued audit logging for manual target and lock config actions.

- Tactical UX
  - Shifted console styling to military HUD visual language.
  - Reticle and panel visuals optimized for operator clarity.
  - Added active/inactive button color cues for fast state recognition.
  - Added tactical text stream panel under camera feed.

### 4) API Hardening and Request Handling Tricks

Added robust request-handling mechanisms:

- Request-ID tracing
  - Accepts or generates X-Request-ID.
  - Echoes X-Request-ID and X-Response-Time-Ms in responses.

- Payload guard
  - Enforces JSON content-type for body-carrying API calls.
  - Rejects oversized payloads with configurable max bytes.

- Scoped rate limiting
  - Keys by IP + role + method + path for fairer throttling.

- Fire endpoint safety
  - Idempotency-Key support to avoid duplicate fire on retries.
  - Cooldown protection to prevent burst trigger requests.

---

## Project Layout

```text
audit_logger.py            # Durable SQLite audit trail
cursor_controller.py       # Desktop fallback controller
gun_controller.py          # Thread-safe servo command layer
visual_stabilizer.py       # Standalone NCC stabilizer mode
web_control_server.py      # FastAPI + Flask + AI target lock integration

templates/index.html       # Main operator dashboard
static/app.js              # Frontend control + API wiring
static/styles.css          # Console styling

templates/self_test.html   # Startup diagnostics page
static/self_test.js        # Self-test logic

tripod_control/            # Arduino firmware
tripod_control_standalone/ # Arduino standalone 6-button + multi-mode firmware
servo_test/                # Arduino servo test sketch
```

---

## API Surface

### Public

- GET /api/health
- GET /api/public-config

### Observer + Operator

- GET /api/state
- GET /api/target-lock/state

### Operator Only

- POST /api/aim
- POST /api/angles
- POST /api/center
- POST /api/fire
- POST /api/self-test
- POST /api/target-lock/config
- POST /api/target-lock/enable
- POST /api/target-lock/disable
- POST /api/target-lock/manual-target
- GET /api/audit

---

## Security and Environment

Set keys before launch:

```bash
export RWS_OPERATOR_KEY="replace-with-operator-key"
export RWS_OBSERVER_KEY="replace-with-observer-key"
export RWS_RATE_LIMIT_PER_MIN=120

# optional hardening controls
export RWS_MAX_API_BODY_BYTES=32768
export RWS_IDEMPOTENCY_TTL_SECONDS=30
export RWS_FIRE_COOLDOWN_SECONDS=0.45
```

If only one key is used:

```bash
export RWS_API_KEY="legacy-single-key"
```

---

## Quick Start

### 1) Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Run Platform

```bash
python3 web_control_server.py
```

### 3) Open Console

- Main UI: http://localhost:8000/
- Diagnostics: http://localhost:8000/self-test

---

## AI Target Lock Usage

1. Open main dashboard.
2. Select lock mode (Face, Centered object, or Manual target locking).
3. Adjust deadzone and pan gain sliders.
4. Tune Auto response for Face/Object modes.
5. If using Manual mode, drag the reticle in the camera window to your desired visual point.
6. Tune Manual response: lower for smoother movement, higher for faster movement.
7. Click Enable Lock.
8. Observe lock state and confidence; tune gain/deadzone/response sliders to minimize overshoot.

Recommended tuning baseline:

- deadzone: 0.05 to 0.08
- pan gain: 1.8 to 2.8
- auto response: 0.30 to 0.55
- manual response: 0.25 to 0.55

---

## How To Use Standalone Mode

Use this mode when you want the Arduino to run locally with buttons, with or without a connected laptop.

### 1) Upload firmware

- Flash `tripod_control_standalone/tripod_control_standalone.ino` to the Arduino.

### 2) Wire buttons (INPUT_PULLUP)

- Connect one side of each push-button to the pin, and the other side to GND.
- Pin map:
  - UP: D2
  - DOWN: D3
  - LEFT: D4
  - RIGHT: D7
  - TRIGGER: D8
  - MODE: D10

No external pull-up resistors are required.

### 3) Servo and trigger pins

- Pan servo: D5
- Tilt servo: D6
- Trigger servo: D9

### 4) Use operating modes

Press MODE to cycle:

- `MANUAL`
  - Hold any direction button for smooth continuous motion.
  - Press two direction buttons together for diagonal motion:
    - LEFT+UP, LEFT+DOWN, RIGHT+UP, RIGHT+DOWN
  - TRIGGER fires a pulse and then returns to safe angle.
- `PATROL`
  - Pan sweeps left-right automatically.
  - UP/DOWN still adjust tilt.
  - TRIGGER remains active.
- `SENTRY`
  - Pan/tilt snap to center on entry.
  - TRIGGER remains active.
  - LEFT/RIGHT allow smooth low-speed pan corrections.

### 7) Standalone motion tuning (optional)

If you want slower/faster or more realistic button response, tune these constants in `tripod_control_standalone/tripod_control_standalone.ino`:

- `MOVEMENT_TICK_MS` (update period, default ~60 Hz)
- `PAN_SPEED_DPS` (pan speed in degrees/second)
- `TILT_SPEED_DPS` (tilt speed in degrees/second)
- `DIAGONAL_SCALE` (diagonal normalization factor)
- `SENTRY_PAN_DPS` (micro-correction speed in SENTRY)

### 5) Understand serial handover

- The firmware still accepts serial packets in the existing format:
  - `PAN,TILT,TRIGGER\n`
- If valid serial packets are arriving, host control takes priority.
- If serial goes idle for the timeout window, control automatically returns to buttons.

### 6) Verify status on Serial Monitor (optional)

- Open Serial Monitor at `9600` baud.
- You will see mode and event messages such as:
  - `MODE:MANUAL`
  - `MODE:PATROL`
  - `MODE:SENTRY`
  - `EVENT:FIRE`
  - `SERIAL_IDLE:BUTTON_CTRL_ACTIVE`

---

## Hardware Contract

Serial frame to Arduino:

```text
PAN,TILT,TRIGGER\n
```

Servo mapping:
- Pan: pin 5
- Tilt: pin 6
- Trigger: pin 9

Ranges:
- Pan: 0..180
- Tilt: 70..110
- Trigger safe/fire: 45 / 135

---

## Validation Checklist

- Verify observer cannot call control endpoints.
- Verify operator can use all control + target lock routes.
- Verify /api/state includes target_lock block.
- Verify target_lock.manual_target_norm updates while dragging reticle in Manual mode.
- Verify target_lock.manual_target_filtered and manual_response in /api/state.
- Verify target_lock.auto_response in /api/state.
- Verify lock enable/disable and mode controls visibly change button/select styles.
- Verify tactical stream panel updates with live telemetry lines.
- Verify fire cooldown and idempotency behavior.
- Verify /api/audit records target lock config events.
- Verify video feed overlay shows lock status, bounding box, and gun-point marker.

---

## Safety Notice

Operate only in legal, controlled, and approved test environments.
Always validate software behavior in preview mode before enabling physical actuation.