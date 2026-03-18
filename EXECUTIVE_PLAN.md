# Executive Delivery Plan

## Objective

Deliver a secure and robust control system with:
- role-based API authorization (operator and observer),
- endpoint-level permission enforcement,
- persistent command audit logging,
- live keyboard sensitivity control in the web UI,
- complete project hardening and regression validation,
- upgraded royal/futuristic interface with line-pattern visual language.

## Scope and Workstreams

1. Security and Access Control
- Introduce two distinct API roles:
  - operator: full control endpoints.
  - observer: read-only telemetry endpoints.
- Add explicit per-endpoint permission policy.
- Return consistent HTTP codes:
  - 401 for missing/invalid credentials.
  - 403 for valid credentials without permission.

2. Persistent Audit Logging
- Add append-only durable audit store (SQLite) for movement and fire commands.
- Capture: timestamp, role, client IP, command, payload, result.
- Add API to retrieve recent audit records for authorized users.

3. Input Control UX
- Add live keyboard sensitivity slider for WASD/arrow joystick mode.
- Persist selected sensitivity locally in browser storage.
- Ensure pointer and keyboard control paths remain unified.

4. Reliability and Loophole Closure
- Validate protected routes are never executable by observer role.
- Validate rate limiting behavior under burst requests.
- Ensure serial auto-detection remains constrained to valid hardware patterns.
- Confirm startup behavior remains safe in preview mode.

5. UI/UX Upgrade
- Shift to classic royal look:
  - deep navy and metallic gold palette,
  - hard-edged panel geometry,
  - decorative line patterns and frame motifs,
  - restrained motion and non-playful interaction cues.
- Preserve responsive behavior on desktop and mobile.

6. Verification and Release
- Run syntax checks and runtime smoke tests.
- Validate endpoints under both roles.
- Verify self-test flow and diagnostics reporting.
- Update README to reflect architecture, security model, and operations.

## Endpoint Permission Matrix (Target)

- Public
  - GET /api/health
  - GET /api/public-config

- Observer and Operator
  - GET /api/state

- Operator only
  - POST /api/aim
  - POST /api/angles
  - POST /api/center
  - POST /api/fire
  - POST /api/self-test
  - GET /api/audit

## Acceptance Criteria

- Role-based auth is active and verified end-to-end.
- Observer cannot execute control commands.
- Fire and movement commands are durably logged and queryable.
- Keyboard sensitivity changes immediately impact keyboard joystick movement.
- UI reflects required royal/futuristic style with line decoration patterns.
- Full project smoke tests pass with no regressions.
