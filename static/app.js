const joystickBase = document.getElementById("joystickBase");
const joystickStick = document.getElementById("joystickStick");
const centerBtn = document.getElementById("centerBtn");
const fireBtn = document.getElementById("fireBtn");
const apiKeyInput = document.getElementById("apiKeyInput");
const saveApiKeyBtn = document.getElementById("saveApiKeyBtn");
const kbSensitivity = document.getElementById("kbSensitivity");
const kbSensitivityValue = document.getElementById("kbSensitivityValue");
const kbHint = document.getElementById("kbHint");

const connStatus = document.getElementById("connStatus");
const angleStatus = document.getElementById("angleStatus");
const targetStatus = document.getElementById("targetStatus");
const cameraStatus = document.getElementById("cameraStatus");
const roleStatus = document.getElementById("roleStatus");

let activePointerId = null;
let joyX = 0;
let joyY = 0;
let lastSendAt = 0;
const pressedKeys = new Set();
let keyStep = 0.6;
let currentRole = "unknown";

function getApiKey() {
  return localStorage.getItem("rwsApiKey") || "";
}

function buildHeaders() {
  const headers = { "Content-Type": "application/json" };
  const apiKey = getApiKey();
  if (apiKey) {
    headers["X-API-Key"] = apiKey;
  }
  return headers;
}

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function applyRolePermissions(role) {
  currentRole = role || "unknown";
  const observerMode = currentRole === "observer";
  centerBtn.disabled = observerMode;
  fireBtn.disabled = observerMode;
  joystickBase.style.opacity = observerMode ? "0.6" : "1";
  roleStatus.textContent = currentRole;
}

async function postJSON(url, payload = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: buildHeaders(),
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(errorText || `Request failed: ${response.status}`);
  }

  return response.json();
}

function moveStick(xNorm, yNorm) {
  joyX = clamp(xNorm, -1, 1);
  joyY = clamp(yNorm, -1, 1);

  const rect = joystickBase.getBoundingClientRect();
  const radius = rect.width / 2;
  const maxOffset = radius * 0.65;

  const xPx = joyX * maxOffset;
  const yPx = joyY * maxOffset;
  joystickStick.style.transform = `translate(calc(-50% + ${xPx}px), calc(-50% + ${yPx}px))`;
}

async function sendAim(force = false) {
  const now = performance.now();
  if (!force && now - lastSendAt < 50) {
    return;
  }

  lastSendAt = now;
  try {
    await postJSON("/api/aim", { x: joyX, y: joyY });
  } catch (err) {
    connStatus.textContent = "API error or unauthorized";
  }
}

function updateFromKeyboard() {
  const left = pressedKeys.has("arrowleft") || pressedKeys.has("a");
  const right = pressedKeys.has("arrowright") || pressedKeys.has("d");
  const up = pressedKeys.has("arrowup") || pressedKeys.has("w");
  const down = pressedKeys.has("arrowdown") || pressedKeys.has("s");

  let x = 0;
  let y = 0;
  if (left && !right) {
    x = -keyStep;
  } else if (right && !left) {
    x = keyStep;
  }

  if (up && !down) {
    y = -keyStep;
  } else if (down && !up) {
    y = keyStep;
  }

  moveStick(x, y);
  sendAim(true);
}

function pointerToNormalized(event) {
  const rect = joystickBase.getBoundingClientRect();
  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;

  const dx = event.clientX - cx;
  const dy = event.clientY - cy;
  const max = rect.width / 2;

  let x = dx / max;
  let y = dy / max;
  const mag = Math.hypot(x, y);
  if (mag > 1) {
    x /= mag;
    y /= mag;
  }

  return { x, y };
}

function onPointerDown(event) {
  if (currentRole === "observer") {
    connStatus.textContent = "Observer role cannot control hardware";
    return;
  }
  activePointerId = event.pointerId;
  joystickBase.setPointerCapture(activePointerId);
  const n = pointerToNormalized(event);
  moveStick(n.x, n.y);
  sendAim(true);
}

function onPointerMove(event) {
  if (event.pointerId !== activePointerId) {
    return;
  }

  const n = pointerToNormalized(event);
  moveStick(n.x, n.y);
  sendAim();
}

function onPointerUp(event) {
  if (event.pointerId !== activePointerId) {
    return;
  }

  joystickBase.releasePointerCapture(activePointerId);
  activePointerId = null;
  moveStick(0, 0);
  sendAim(true);
}

joystickBase.addEventListener("pointerdown", onPointerDown);
joystickBase.addEventListener("pointermove", onPointerMove);
joystickBase.addEventListener("pointerup", onPointerUp);
joystickBase.addEventListener("pointercancel", onPointerUp);

window.addEventListener("keydown", (event) => {
  const key = event.key.toLowerCase();
  if (["arrowleft", "arrowright", "arrowup", "arrowdown", "w", "a", "s", "d"].includes(key)) {
    if (currentRole === "observer") {
      return;
    }
    event.preventDefault();
    pressedKeys.add(key);
    updateFromKeyboard();
  }
});

window.addEventListener("keyup", (event) => {
  const key = event.key.toLowerCase();
  if (["arrowleft", "arrowright", "arrowup", "arrowdown", "w", "a", "s", "d"].includes(key)) {
    event.preventDefault();
    pressedKeys.delete(key);
    updateFromKeyboard();
  }
});

centerBtn.addEventListener("click", async () => {
  if (currentRole === "observer") {
    connStatus.textContent = "Observer role cannot control hardware";
    return;
  }
  moveStick(0, 0);
  try {
    await postJSON("/api/center");
  } catch (err) {
    connStatus.textContent = "Center command failed";
  }
});

saveApiKeyBtn.addEventListener("click", () => {
  const value = (apiKeyInput.value || "").trim();
  if (value) {
    localStorage.setItem("rwsApiKey", value);
    connStatus.textContent = "API key saved";
  } else {
    localStorage.removeItem("rwsApiKey");
    connStatus.textContent = "API key cleared";
  }
});

fireBtn.addEventListener("click", async () => {
  if (currentRole === "observer") {
    connStatus.textContent = "Observer role cannot control hardware";
    return;
  }
  fireBtn.disabled = true;
  try {
    await postJSON("/api/fire");
  } catch (err) {
    connStatus.textContent = "Fire command failed";
  } finally {
    setTimeout(() => {
      fireBtn.disabled = false;
    }, 400);
  }
});

kbSensitivity.addEventListener("input", () => {
  keyStep = clamp(parseFloat(kbSensitivity.value || "0.6"), 0.15, 1.0);
  kbSensitivityValue.textContent = keyStep.toFixed(2);
  kbHint.textContent = `Keyboard step: ${keyStep.toFixed(2)} normalized units`;
  localStorage.setItem("rwsKeyboardStep", keyStep.toFixed(2));
});

async function refreshState() {
  try {
    const response = await fetch("/api/state", { cache: "no-store", headers: buildHeaders() });
    if (!response.ok) {
      if (response.status === 401) {
        throw new Error("Unauthorized");
      }
      if (response.status === 403) {
        throw new Error("Forbidden");
      }
      throw new Error("State request failed");
    }

    const state = await response.json();

    connStatus.textContent = state.connected
      ? `Connected (${state.serial_port || "auto"})`
      : "Preview mode (no serial)";
    angleStatus.textContent = `${state.current_pan.toFixed(1)} / ${state.current_tilt.toFixed(1)}`;
    targetStatus.textContent = `${state.target_pan.toFixed(1)} / ${state.target_tilt.toFixed(1)}`;

    const camera = state.camera || {};
    if (camera.ready && camera.resolution?.width && camera.resolution?.height) {
      cameraStatus.textContent = `${camera.resolution.width}x${camera.resolution.height}`;
    } else if (camera.last_error) {
      cameraStatus.textContent = camera.last_error;
    } else {
      cameraStatus.textContent = "Starting...";
    }

    applyRolePermissions(state.role || "operator");
  } catch (err) {
    if (err.message === "Unauthorized") {
      connStatus.textContent = "Unauthorized (set API key)";
    } else {
      connStatus.textContent = "Disconnected";
    }
    cameraStatus.textContent = "State unavailable";
    roleStatus.textContent = "Unknown";
  }
}

moveStick(0, 0);
apiKeyInput.value = getApiKey();
const savedStep = parseFloat(localStorage.getItem("rwsKeyboardStep") || "0.60");
keyStep = clamp(savedStep, 0.15, 1.0);
kbSensitivity.value = keyStep.toFixed(2);
kbSensitivityValue.textContent = keyStep.toFixed(2);
kbHint.textContent = `Keyboard step: ${keyStep.toFixed(2)} normalized units`;
refreshState();
setInterval(refreshState, 1000);
