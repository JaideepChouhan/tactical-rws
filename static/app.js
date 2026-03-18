const joystickBase = document.getElementById("joystickBase");
const joystickStick = document.getElementById("joystickStick");
const centerBtn = document.getElementById("centerBtn");
const fireBtn = document.getElementById("fireBtn");
const apiKeyInput = document.getElementById("apiKeyInput");
const saveApiKeyBtn = document.getElementById("saveApiKeyBtn");
const kbSensitivity = document.getElementById("kbSensitivity");
const kbSensitivityValue = document.getElementById("kbSensitivityValue");
const kbHint = document.getElementById("kbHint");

const lockMode = document.getElementById("lockMode");
const lockDeadzone = document.getElementById("lockDeadzone");
const lockDeadzoneValue = document.getElementById("lockDeadzoneValue");
const lockGain = document.getElementById("lockGain");
const lockGainValue = document.getElementById("lockGainValue");
const lockEnableBtn = document.getElementById("lockEnableBtn");
const lockDisableBtn = document.getElementById("lockDisableBtn");

const connStatus = document.getElementById("connStatus");
const angleStatus = document.getElementById("angleStatus");
const targetStatus = document.getElementById("targetStatus");
const cameraStatus = document.getElementById("cameraStatus");
const roleStatus = document.getElementById("roleStatus");
const lockModeStatus = document.getElementById("lockModeStatus");
const lockStateStatus = document.getElementById("lockStateStatus");
const lockConfidenceStatus = document.getElementById("lockConfidenceStatus");

let activePointerId = null;
let joyX = 0;
let joyY = 0;
let lastSendAt = 0;
const pressedKeys = new Set();
let keyStep = 0.6;
let currentRole = "unknown";

const RETRYABLE_STATUS = new Set([408, 429, 500, 502, 503, 504]);

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function randomId(prefix = "req") {
  const token = Math.random().toString(36).slice(2, 10);
  return `${prefix}-${Date.now()}-${token}`;
}

function getApiKey() {
  return localStorage.getItem("rwsApiKey") || "";
}

function buildHeaders(extraHeaders = {}) {
  const headers = {
    "Content-Type": "application/json",
    "X-Request-ID": randomId("ui"),
    ...extraHeaders,
  };
  const apiKey = getApiKey();
  if (apiKey) {
    headers["X-API-Key"] = apiKey;
  }
  return headers;
}

async function fetchWithTimeout(url, options = {}, timeoutMs = 3500) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function requestJSON(url, { method = "GET", payload = null, timeoutMs = 3500, retries = 1, extraHeaders = {} } = {}) {
  const headers = buildHeaders(extraHeaders);
  const options = { method, headers, cache: "no-store" };
  if (payload !== null) {
    options.body = JSON.stringify(payload);
  }

  let lastError = null;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      const response = await fetchWithTimeout(url, options, timeoutMs);
      if (!response.ok) {
        if (RETRYABLE_STATUS.has(response.status) && attempt < retries) {
          await new Promise((resolve) => setTimeout(resolve, 140 * (attempt + 1)));
          continue;
        }
        const body = await response.text();
        throw new Error(body || `HTTP ${response.status}`);
      }
      return await response.json();
    } catch (err) {
      lastError = err;
      if (attempt < retries) {
        await new Promise((resolve) => setTimeout(resolve, 140 * (attempt + 1)));
        continue;
      }
    }
  }

  throw lastError || new Error("Request failed");
}

function applyRolePermissions(role) {
  currentRole = role || "unknown";
  const observerMode = currentRole === "observer";

  centerBtn.disabled = observerMode;
  fireBtn.disabled = observerMode;
  lockEnableBtn.disabled = observerMode;
  lockDisableBtn.disabled = observerMode;
  lockMode.disabled = observerMode;
  lockDeadzone.disabled = observerMode;
  lockGain.disabled = observerMode;

  joystickBase.style.opacity = observerMode ? "0.6" : "1";
  roleStatus.textContent = currentRole;
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
    await requestJSON("/api/aim", { method: "POST", payload: { x: joyX, y: joyY }, retries: 1 });
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
    await requestJSON("/api/center", { method: "POST", payload: {}, retries: 1 });
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
    await requestJSON("/api/fire", {
      method: "POST",
      payload: {},
      retries: 0,
      extraHeaders: { "Idempotency-Key": randomId("fire") },
    });
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

lockDeadzone.addEventListener("input", () => {
  lockDeadzoneValue.textContent = Number(lockDeadzone.value).toFixed(2);
});

lockGain.addEventListener("input", () => {
  lockGainValue.textContent = Number(lockGain.value).toFixed(1);
});

async function pushTargetLockConfig(partial) {
  if (currentRole === "observer") {
    return;
  }
  try {
    const data = await requestJSON("/api/target-lock/config", {
      method: "POST",
      payload: partial,
      retries: 1,
    });

    lockModeStatus.textContent = (data.mode || "face").toUpperCase();
    lockStateStatus.textContent = data.enabled ? (data.locked ? "Locked" : "Searching") : "Disabled";
    lockConfidenceStatus.textContent = (data.confidence || 0).toFixed(2);
  } catch (err) {
    connStatus.textContent = "Target lock config failed";
  }
}

lockEnableBtn.addEventListener("click", async () => {
  await pushTargetLockConfig({
    enabled: true,
    mode: lockMode.value,
    deadzone: parseFloat(lockDeadzone.value),
    kp_pan: parseFloat(lockGain.value),
  });
});

lockDisableBtn.addEventListener("click", async () => {
  try {
    const data = await requestJSON("/api/target-lock/disable", {
      method: "POST",
      payload: {},
      retries: 1,
    });
    lockStateStatus.textContent = data.enabled ? "Searching" : "Disabled";
  } catch (err) {
    connStatus.textContent = "Failed to disable target lock";
  }
});

lockMode.addEventListener("change", async () => {
  await pushTargetLockConfig({ mode: lockMode.value });
});

async function refreshState() {
  try {
    const state = await requestJSON("/api/state", { method: "GET", retries: 1, timeoutMs: 3000 });

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

    const lock = state.target_lock || {};
    const lockEnabled = Boolean(lock.enabled);
    const lockLocked = Boolean(lock.locked);
    lockModeStatus.textContent = (lock.mode || "face").toUpperCase();
    lockStateStatus.textContent = lockEnabled ? (lockLocked ? "Locked" : "Searching") : "Disabled";
    lockConfidenceStatus.textContent = Number(lock.confidence || 0).toFixed(2);

    if (typeof lock.deadzone === "number") {
      lockDeadzone.value = String(lock.deadzone.toFixed(2));
      lockDeadzoneValue.textContent = lock.deadzone.toFixed(2);
    }
    if (typeof lock.kp_pan === "number") {
      lockGain.value = String(lock.kp_pan.toFixed(1));
      lockGainValue.textContent = lock.kp_pan.toFixed(1);
    }
    if (lock.mode) {
      lockMode.value = lock.mode;
    }

    applyRolePermissions(state.role || "operator");
  } catch (err) {
    const msg = String(err.message || "");
    if (msg.toLowerCase().includes("unauthorized")) {
      connStatus.textContent = "Unauthorized (set API key)";
    } else {
      connStatus.textContent = "Disconnected";
    }
    cameraStatus.textContent = "State unavailable";
    roleStatus.textContent = "Unknown";
    lockStateStatus.textContent = "Unavailable";
  }
}

moveStick(0, 0);
apiKeyInput.value = getApiKey();
const savedStep = parseFloat(localStorage.getItem("rwsKeyboardStep") || "0.60");
keyStep = clamp(savedStep, 0.15, 1.0);
kbSensitivity.value = keyStep.toFixed(2);
kbSensitivityValue.textContent = keyStep.toFixed(2);
kbHint.textContent = `Keyboard step: ${keyStep.toFixed(2)} normalized units`;

lockDeadzoneValue.textContent = Number(lockDeadzone.value).toFixed(2);
lockGainValue.textContent = Number(lockGain.value).toFixed(1);

refreshState();
setInterval(refreshState, 1000);