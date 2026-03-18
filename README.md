# AIR0214 Remote Weapon Control Platform

A secure remote control software stack for pan/tilt/trigger systems with a dual-layer web architecture (Flask UI + FastAPI API), role-based access control, persistent audit trails, startup diagnostics, and optical stabilization support.

## 1. Core Capabilities

- Role-based authorization with operator and observer roles.
- Endpoint-level permission enforcement.
- API rate limiting per client IP.
- Persistent audit logging for fire and movement command paths.
- Live camera stream in a responsive control console.
- 2D joystick control via pointer and keyboard (WASD or arrow keys).
- Live keyboard sensitivity slider with local persistence.
- Startup self-test page for camera, serial, and command-path diagnostics.
- NCC visual stabilizer mode for target-lock compensation.

## 2. Architecture

- gun_controller.py
  - Serial communication, smoothing loop, clamping, trigger sequence, movement self-test.

- web_control_server.py
  - FastAPI endpoints, RBAC middleware, rate limiting, command routing, Flask mounting.

- audit_logger.py
  - SQLite-backed durable audit records.
  - Bounded retention to avoid unbounded disk growth.

- templates/index.html + static/app.js + static/styles.css
  - Main royal-themed operator console with keyboard sensitivity controls.

- templates/self_test.html + static/self_test.js
  - Automated diagnostic dashboard.

- cursor_controller.py
  - Desktop fallback terminal and mouse control modes.

- visual_stabilizer.py
  - OpenCV NCC-based stabilization mode.

- tripod_control/tripod_control.ino
  - Arduino serial parser and servo command output.

## 3. Access Model and Permissions

### Roles

- operator
  - Full control and diagnostics.

- observer
  - Telemetry-only role.

### Endpoint Permission Matrix

Public:
- GET /api/health
- GET /api/public-config

Operator and Observer:
- GET /api/state

Operator only:
- POST /api/aim
- POST /api/angles
- POST /api/center
- POST /api/fire
- POST /api/self-test
- GET /api/audit

### HTTP Semantics

- 401 Unauthorized: missing/invalid key on protected route.
- 403 Forbidden: authenticated but role lacks endpoint permission.
- 429 Too Many Requests: per-IP rate limit exceeded.

## 4. Security Configuration

Environment variables:

- RWS_OPERATOR_KEY
  - API key for operator role.

- RWS_OBSERVER_KEY
  - API key for observer role.

- RWS_API_KEY
  - Legacy fallback key; mapped to operator if RWS_OPERATOR_KEY is not set.

- RWS_RATE_LIMIT_PER_MIN
  - Request limit per minute on protected API routes.
  - Default: 120

Example secure startup:

- export RWS_OPERATOR_KEY="replace-with-operator-key"
- export RWS_OBSERVER_KEY="replace-with-observer-key"
- export RWS_RATE_LIMIT_PER_MIN=120
- python3 web_control_server.py

## 5. API Overview

- GET /api/health
  - Runtime health, auth mode, and camera/controller state.

- GET /api/public-config
  - Public security and role capability metadata.

- GET /api/state
  - Current pan/tilt state, camera status, and resolved role.

- POST /api/aim
  - Body: {"x": -1..1, "y": -1..1}

- POST /api/angles
  - Body: {"pan": 0..180, "tilt": 70..110}

- POST /api/center

- POST /api/fire

- POST /api/self-test

- GET /api/audit?limit=100
  - Returns recent command events (max 500).

## 6. Audit Logging

Persistence backend:
- SQLite file: audit_logs.db

Logged fields:
- id
- ts (unix epoch)
- role
- client_ip
- command
- payload
- result

Retention:
- Auto-pruned to latest 50,000 records.

## 7. Web Console Features

Main console URL:
- http://localhost:8000/

Features:
- TV-style live video pane.
- Pointer-based 2D joystick.
- Keyboard joystick with WASD / arrow keys.
- Live keyboard sensitivity slider (0.15 to 1.00).
- API key save/clear (browser local storage).
- Role-aware control lockout for observer sessions.

Startup diagnostics URL:
- http://localhost:8000/self-test

Self-test output:
- Auth mode visibility.
- Camera readiness and resolution.
- Serial mode (connected or preview).
- Movement cycle result.
- Raw diagnostic JSON output.

## 8. Hardware Contract

Servo pin mapping:
- Pan: pin 5
- Tilt: pin 6
- Trigger: pin 9

Serial payload:
- PAN,TILT,TRIGGER\n
Ranges:
- Pan: 0 to 180
- Tilt: 70 to 110
- Trigger: 45 safe, 135 fire

## 9. Installation and Run

### Dependencies

- python3 -m venv .venv
- source .venv/bin/activate
- pip install -r requirements.txt

### Run Web Platform

- python3 web_control_server.py

### Run Desktop Fallback Controller

Terminal mode:
- python3 cursor_controller.py --mode terminal

Mouse mode:
- python3 cursor_controller.py --mode mouse

### Run NCC Stabilizer

- python3 visual_stabilizer.py

## 10. Validation Checklist

Before deployment:
- Verify /api/public-config reports expected auth settings.
- Validate observer key cannot access control endpoints.
- Validate operator key can execute control endpoints.
- Verify /api/audit records fire and movement commands.
- Run /self-test and confirm camera and serial state output.
- Confirm UI keyboard sensitivity updates movement response.

## 11. Safety and Compliance

Operate only in lawful, controlled, and approved testing environments.
Always validate software in preview mode before enabling physical actuation.
