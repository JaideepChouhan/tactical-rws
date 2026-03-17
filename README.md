# Tactical Remote Weapon Station (RWS) — AIR0214

A 3D-printed remote weapon station with two control modes: **mouse-based targeting** and **NCC visual stabilization**. The system drives three servos (pan, tilt, trigger) on an Arduino UNO via serial communication.

## ⚠️ Important Notice

This project is designed for **military/defense research applications** and must be used in accordance with all applicable laws and regulations.

---

## 📂 Project Structure

```
├── cursor_controller.py        # Mode 1 — Mouse-based pan/tilt targeting
├── visual_stabilizer.py        # Mode 2 — NCC visual odometry stabilization ✨ NEW
├── tripod_control/
│   └── tripod_control.ino      # Arduino firmware (serial → 3 servos)
├── servo_test/
│   └── servo_test.ino          # Simple servo centering test sketch
└── README.md                   # This file
```

---

## 🎯 System Capabilities

| Feature | `cursor_controller.py` | `visual_stabilizer.py` |
|---------|----------------------|----------------------|
| Input | Mouse cursor | Laptop webcam (OpenCV) |
| Tracking | Manual aiming | Automatic — NCC template matching |
| Use-case | Direct remote aiming | Body-motion-compensated stabilization |
| Trigger | Right-click | Press `f` key |
| Update Rate | ~50 Hz | ~30 Hz (camera-bound) |

### Visual Stabilizer — How It Works

The gun must keep pointing at the **same world point** even when the operator's body (and thus the camera mounted alongside the gun) changes orientation. This is achieved via **Normalized Cross-Correlation (NCC) visual odometry**:

1. **Lock target** — The operator presses `r`; a square template patch is captured from the center of the webcam frame.
2. **Track displacement** — Each new frame is searched using `cv2.matchTemplate(TM_CCORR_NORMED)` within a bounded search region around the lock point. The best-match location gives the pixel displacement `(dx, dy)`.
3. **Pixels → Degrees** — Pixel displacement is converted to angular displacement using the camera's known field-of-view (`CAMERA_HFOV`, `CAMERA_VFOV`).
4. **Compensate servos** — The angular offset is applied in the **opposite** direction to the servo targets, cancelling out the body's rotation and keeping the gun on target.
5. **Smoothing** — A configurable low-pass filter prevents servo jitter.
6. **Auto-refresh** — Every N frames the template is re-acquired at the current match location to prevent drift accumulation. Angular offsets are carried across refreshes so servos don't snap.

#### NCC Algorithm Detail

```
Template T (128×128 grayscale patch, captured at lock time)
Frame F (current grayscale frame)

For each candidate position (u, v) in the search region:
                    Σ [ T(x,y) · F(x+u, y+v) ]
  NCC(u,v) = ─────────────────────────────────────────
              √[ Σ T(x,y)² ] · √[ Σ F(x+u, y+v)² ]

Best match = argmax NCC(u,v)
Displacement = (best_u - lock_x, best_v - lock_y)
```

OpenCV's `TM_CCORR_NORMED` implements this efficiently. A confidence threshold (`NCC_THRESHOLD = 0.4`) rejects unreliable matches (e.g., heavy occlusion or blur).

---

## 🔧 Technical Specifications

| Component | Specification |
|-----------|--------------|
| Pan Range | 0° – 180° (servo on pin 5) |
| Tilt Range | 70° – 110° / 40° operational (servo on pin 6) |
| Trigger Travel | 45° safe → 135° fire (servo on pin 9) |
| Serial Protocol | `"PAN,TILT,TRIGGER\n"` at 9600 baud |
| Camera Resolution | 640×480 (configurable) |
| Camera FOV | ~60° H × ~45° V (configurable) |
| Template Size | 64–256 px (adjustable at runtime with `+`/`-`) |
| Smoothing | Low-pass filter, α = 0.25 |

### Serial Protocol

The Python scripts communicate with the Arduino UNO over USB serial at **9600 baud**. Each command is a newline-terminated CSV string:

```
<pan_angle>,<tilt_angle>,<trigger_angle>\n
```

- **pan_angle**: 0–180 (integer)
- **tilt_angle**: 70–110 (integer, constrained on Arduino)
- **trigger_angle**: 45 (safe) or 135 (fire)

The Arduino firmware (`tripod_control.ino`) parses this with `Serial.readStringUntil('\n')`, extracts the three values, constrains them, and writes them to the servos.

---

## 📦 Hardware Requirements

- **Arduino UNO** (or Nano)
- **3× Servo motors** (metal-gear recommended for stability)
  - Pan servo → pin 5
  - Tilt servo → pin 6
  - Trigger servo → pin 9
- **Laptop with webcam** (for `visual_stabilizer.py`)
- **USB cable** (Arduino ↔ laptop)
- **5V / 2A+ power supply** for servos (do NOT power from Arduino 5V pin under load)
- **3D-printed mount** (designed in Blender)

---

## 🚀 Getting Started

### 1. Flash the Arduino

Open `tripod_control/tripod_control.ino` in the Arduino IDE, select your board (UNO) and port, then upload.

### 2. Install Python Dependencies

```bash
pip install opencv-python numpy pyserial
# For cursor_controller.py only:
pip install pyautogui pynput
```

### 3. Find Your Serial Port

```bash
# Linux
ls /dev/ttyUSB* /dev/ttyACM*

# macOS
ls /dev/cu.usbmodem*

# Windows → check Device Manager for COMx
```

Edit `SERIAL_PORT` in the script to match (default: `/dev/ttyUSB0`).

### 4. Run the Visual Stabilizer

```bash
python3 visual_stabilizer.py
```

**Controls:**

| Key | Action |
|-----|--------|
| `r` | Lock / re-lock target at frame center |
| `f` | Fire trigger (45° → 135° → 45°) |
| `+` / `-` | Increase / decrease template patch size |
| `q` / `ESC` | Quit (servos return to center) |

The OpenCV window shows a live HUD with:
- Green crosshair (frame center)
- Cyan rectangle (locked template region)
- Red rectangle (current match position)
- Yellow arrow (displacement vector)
- Status, NCC confidence, displacement, servo angles, FPS

### 5. Run the Mouse Controller (original mode)

```bash
python3 cursor_controller.py
```

Move the mouse to aim; right-click to fire.

---

## 🔬 Configuration Reference

All tunable parameters are at the top of `visual_stabilizer.py`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `SERIAL_PORT` | `/dev/ttyUSB0` | Arduino serial port |
| `BAUDRATE` | `9600` | Must match Arduino `Serial.begin()` |
| `CAMERA_INDEX` | `0` | OpenCV camera index |
| `CAMERA_WIDTH/HEIGHT` | `640×480` | Requested camera resolution |
| `TEMPLATE_SIZE` | `128` | NCC template patch side length (px) |
| `CAMERA_HFOV` | `60.0` | Horizontal field of view (degrees) |
| `CAMERA_VFOV` | `45.0` | Vertical field of view (degrees) |
| `SMOOTHING` | `0.25` | Low-pass filter coefficient (0–1) |
| `NCC_THRESHOLD` | `0.4` | Minimum NCC confidence to trust a match |
| `TEMPLATE_REFRESH_INTERVAL` | `300` | Frames between auto-refresh (0 = off) |

---

## 🛠️ Troubleshooting

| Problem | Solution |
|---------|----------|
| `[ERROR] Cannot open camera` | Check `CAMERA_INDEX`, try `1` or `2`; ensure no other app is using the camera |
| `[ERROR] Could not open /dev/ttyUSB0` | Check port name; add yourself to `dialout` group: `sudo usermod -aG dialout $USER` then re-login |
| Servos jittering | Increase `SMOOTHING` (e.g., `0.15`); use external 5V supply for servos |
| Low NCC confidence | Ensure good lighting; avoid pointing camera at featureless surfaces; increase `TEMPLATE_SIZE` |
| Tracking drifts over time | Decrease `TEMPLATE_REFRESH_INTERVAL` (e.g., `150`); ensure camera is rigidly mounted |
| Wrong compensation direction | Swap sign in `target_pan`/`target_tilt` formulas (depends on physical servo orientation) |

---

## 🏗️ Mechanical Design

All components designed from scratch in **Blender**:
- Rigid mounting platform for recoil management
- Zero-backlash servo mounting points
- Modular design for maintenance and upgrades

---

## 🤖 About the Developer

The **visual stabilizer module** (`visual_stabilizer.py`) was designed and implemented by **GitHub Copilot**, powered by **Claude Opus 4.6** (model ID: `claude-opus-4.6`).

### How I (Copilot) Implemented This Task

1. **Codebase analysis** — I read every file in the project (`cursor_controller.py`, `tripod_control.ino`, `servo_test.ino`, existing `README.md`) to understand the serial communication protocol (`"PAN,TILT,TRIGGER\n"` at 9600 baud), servo pin assignments, angle constraints, and the smoothing/threading architecture.

2. **Algorithm selection** — The requirement specified **Normalized Cross-Correlation (NCC)** for visual odometry. I chose OpenCV's `cv2.matchTemplate` with `TM_CCORR_NORMED` — a hardware-accelerated implementation of the NCC formula that is robust to brightness changes.

3. **Architecture design** — I modeled the system as:
   - An `NCCTracker` class encapsulating template capture, NCC matching, search-region bounding, and auto-refresh with accumulated offset tracking.
   - A background `serial_sender` thread running the same low-pass-filtered servo update loop as the original `cursor_controller.py` (consistent design).
   - A main loop that captures frames, runs NCC tracking, converts pixel displacement to angular compensation, and renders a HUD.

4. **Compensation direction analysis** — I reasoned through the physical geometry: when the body (with camera + gun base) rotates right, the scene shifts left in the frame (`dx < 0`). To keep the gun on target, the servo must counter-rotate left (decrease pan). This gives: `target_pan = CENTER + d_pan_deg`. Similarly, body tilt up → scene shifts down → `target_tilt = CENTER - d_tilt_deg`.

5. **Drift prevention** — Naïve template refresh resets `dx` to zero, snapping servos to center. I solved this by accumulating angular offsets across refreshes: before re-locking, the current displacement is converted to degrees and added to `accum_pan_deg`/`accum_tilt_deg`. The main loop uses `total = accumulated + current`.

6. **Self-review** — I validated syntax (`py_compile`), verified all imports are available, performed AST analysis to confirm all functions/classes are defined, and manually traced the compensation logic for both pan and tilt axes.

---

## 📜 License

This project is part of the **AIR0214** defense research initiative. Use responsibly and in compliance with all applicable regulations.
