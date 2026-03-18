const joystickBase = document.getElementById("joystickBase");
const joystickStick = document.getElementById("joystickStick");
const centerBtn = document.getElementById("centerBtn");
const fireBtn = document.getElementById("fireBtn");
const apiKeyInput = document.getElementById("apiKeyInput");
const saveApiKeyBtn = document.getElementById("saveApiKeyBtn");
const kbSensitivity = document.getElementById("kbSensitivity");
const kbSensitivityValue = document.getElementById("kbSensitivityValue");
const kbHint = document.getElementById("kbHint");
const cameraFeed = document.getElementById("cameraFeed");
const manualTargetReticle = document.getElementById("manualTargetReticle");
const tacticalStream = document.getElementById("tacticalStream");
const tacticalClock = document.getElementById("tacticalClock");

const lockMode = document.getElementById("lockMode");
const lockDeadzone = document.getElementById("lockDeadzone");
const lockDeadzoneValue = document.getElementById("lockDeadzoneValue");
const lockGain = document.getElementById("lockGain");
const lockGainValue = document.getElementById("lockGainValue");
const autoResponse = document.getElementById("autoResponse");
const autoResponseValue = document.getElementById("autoResponseValue");
const manualResponse = document.getElementById("manualResponse");
const manualResponseValue = document.getElementById("manualResponseValue");
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
let currentLockMode = "face";
let manualTargetNorm = { x: 0.5, y: 0.5 };
let manualDragPointerId = null;
let lastManualTargetSentAt = 0;
let manualTargetInFlight = false;
let queuedManualTarget = null;
const tacticalLog = [];
let lastTelemetryLogAt = 0;

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
  autoResponse.disabled = observerMode;
  manualResponse.disabled = observerMode;

  joystickBase.style.opacity = observerMode ? "0.6" : "1";
  roleStatus.textContent = currentRole;
  if (observerMode) {
    manualTargetReticle.classList.remove("visible");
  }
}

function updateButtonStates(lockEnabled) {
  lockEnableBtn.classList.toggle("active-state", lockEnabled);
  lockEnableBtn.classList.toggle("inactive-state", !lockEnabled);
  lockDisableBtn.classList.toggle("active-state", !lockEnabled);
  lockDisableBtn.classList.toggle("inactive-state", lockEnabled);
  lockMode.classList.toggle("active-state", lockEnabled);
}

function appendTacticalLine(line) {
  const now = new Date();
  tacticalClock.textContent = now.toLocaleTimeString();
  const stamped = `[${now.toLocaleTimeString()}] ${line}`;
  tacticalLog.push(stamped);
  if (tacticalLog.length > 14) {
    tacticalLog.shift();
  }
  tacticalStream.textContent = tacticalLog.join("\n");
  tacticalStream.scrollTop = tacticalStream.scrollHeight;
}

function pulseControl(element) {
  if (!element) {
    return;
  }
  element.classList.add("pressed-state");
  setTimeout(() => element.classList.remove("pressed-state"), 140);
}

function updateManualReticlePosition() {
  const frameWidth = Math.max(1, cameraFeed.clientWidth);
  const frameHeight = Math.max(1, cameraFeed.clientHeight);
  const left = cameraFeed.offsetLeft + manualTargetNorm.x * frameWidth;
  const top = cameraFeed.offsetTop + manualTargetNorm.y * frameHeight;

  manualTargetReticle.style.left = `${left}px`;
  manualTargetReticle.style.top = `${top}px`;
}

function setManualReticleVisibility() {
  const visible = currentLockMode === "manual" && currentRole !== "observer";
  manualTargetReticle.classList.toggle("visible", visible);
}

function pointToManualTarget(event) {
  const frameRect = cameraFeed.getBoundingClientRect();
  const x = clamp((event.clientX - frameRect.left) / Math.max(frameRect.width, 1), 0, 1);
  const y = clamp((event.clientY - frameRect.top) / Math.max(frameRect.height, 1), 0, 1);
  return { x, y };
}

async function pushManualTarget(point, force = false) {
  const now = performance.now();
  if (!force && now - lastManualTargetSentAt < 70) {
    return;
  }
  lastManualTargetSentAt = now;
  manualTargetNorm = { x: point.x, y: point.y };
  updateManualReticlePosition();

  if (currentRole === "observer") {
    return;
  }

  queuedManualTarget = { x: point.x, y: point.y };
  if (manualTargetInFlight) {
    return;
  }

  while (queuedManualTarget) {
    const nextPoint = queuedManualTarget;
    queuedManualTarget = null;
    manualTargetInFlight = true;
    try {
      await requestJSON("/api/target-lock/manual-target", {
        method: "POST",
        payload: { x: nextPoint.x, y: nextPoint.y },
        retries: 1,
      });
    } catch (err) {
      connStatus.textContent = "Manual target update failed";
      break;
    } finally {
      manualTargetInFlight = false;
    }
  }
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
  pulseControl(centerBtn);
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
  pulseControl(fireBtn);
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

autoResponse.addEventListener("input", () => {
  autoResponseValue.textContent = Number(autoResponse.value).toFixed(2);
});

autoResponse.addEventListener("change", async () => {
  if (currentRole === "observer") {
    return;
  }
  await pushTargetLockConfig({ auto_response: parseFloat(autoResponse.value) });
});

manualResponse.addEventListener("input", () => {
  manualResponseValue.textContent = Number(manualResponse.value).toFixed(2);
});

manualResponse.addEventListener("change", async () => {
  if (currentRole === "observer") {
    return;
  }
  await pushTargetLockConfig({ manual_response: parseFloat(manualResponse.value) });
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

    currentLockMode = data.mode || currentLockMode;
    lockModeStatus.textContent = (data.mode || "face").toUpperCase();
    lockStateStatus.textContent = data.enabled ? (data.locked ? "Locked" : "Searching") : "Disabled";
    lockConfidenceStatus.textContent = (data.confidence || 0).toFixed(2);
    if (typeof data.auto_response === "number") {
      autoResponse.value = String(data.auto_response.toFixed(2));
      autoResponseValue.textContent = data.auto_response.toFixed(2);
    }
    if (typeof data.manual_response === "number") {
      manualResponse.value = String(data.manual_response.toFixed(2));
      manualResponseValue.textContent = data.manual_response.toFixed(2);
    }
    updateButtonStates(Boolean(data.enabled));
    appendTacticalLine(`LOCK CFG mode=${currentLockMode.toUpperCase()} enabled=${Boolean(data.enabled)}`);
  } catch (err) {
    connStatus.textContent = "Target lock config failed";
  }
}

lockEnableBtn.addEventListener("click", async () => {
  pulseControl(lockEnableBtn);
  await pushTargetLockConfig({
    enabled: true,
    mode: lockMode.value,
    deadzone: parseFloat(lockDeadzone.value),
    kp_pan: parseFloat(lockGain.value),
    auto_response: parseFloat(autoResponse.value),
    manual_response: parseFloat(manualResponse.value),
  });
  setManualReticleVisibility();
});

lockDisableBtn.addEventListener("click", async () => {
  pulseControl(lockDisableBtn);
  try {
    const data = await requestJSON("/api/target-lock/disable", {
      method: "POST",
      payload: {},
      retries: 1,
    });
    lockStateStatus.textContent = data.enabled ? "Searching" : "Disabled";
    updateButtonStates(Boolean(data.enabled));
    appendTacticalLine("LOCK DISABLED");
  } catch (err) {
    connStatus.textContent = "Failed to disable target lock";
  }
});

lockMode.addEventListener("change", async () => {
  pulseControl(lockMode);
  await pushTargetLockConfig({ mode: lockMode.value });
  currentLockMode = lockMode.value;
  setManualReticleVisibility();
  if (currentLockMode === "manual") {
    pushManualTarget(manualTargetNorm, true);
  }
  appendTacticalLine(`MODE -> ${currentLockMode.toUpperCase()}`);
});

manualTargetReticle.addEventListener("pointerdown", async (event) => {
  if (currentRole === "observer" || currentLockMode !== "manual") {
    return;
  }
  manualDragPointerId = event.pointerId;
  manualTargetReticle.setPointerCapture(event.pointerId);
  const point = pointToManualTarget(event);
  pushManualTarget(point, true);
});

manualTargetReticle.addEventListener("pointermove", async (event) => {
  if (event.pointerId !== manualDragPointerId) {
    return;
  }
  const point = pointToManualTarget(event);
  pushManualTarget(point, false);
});

manualTargetReticle.addEventListener("pointerup", async (event) => {
  if (event.pointerId !== manualDragPointerId) {
    return;
  }
  manualTargetReticle.releasePointerCapture(event.pointerId);
  manualDragPointerId = null;
  const point = pointToManualTarget(event);
  pushManualTarget(point, true);
});

manualTargetReticle.addEventListener("pointercancel", (event) => {
  if (event.pointerId !== manualDragPointerId) {
    return;
  }
  manualTargetReticle.releasePointerCapture(event.pointerId);
  manualDragPointerId = null;
});

cameraFeed.addEventListener("click", async (event) => {
  if (currentRole === "observer" || currentLockMode !== "manual") {
    return;
  }
  const point = pointToManualTarget(event);
  pushManualTarget(point, true);
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
    currentLockMode = lock.mode || "face";
    lockModeStatus.textContent = currentLockMode.toUpperCase();
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
    if (typeof lock.auto_response === "number") {
      autoResponse.value = String(lock.auto_response.toFixed(2));
      autoResponseValue.textContent = lock.auto_response.toFixed(2);
    }
    if (typeof lock.manual_response === "number") {
      manualResponse.value = String(lock.manual_response.toFixed(2));
      manualResponseValue.textContent = lock.manual_response.toFixed(2);
    }
    if (lock.mode) {
      lockMode.value = lock.mode;
    }
    if (Array.isArray(lock.manual_target_norm) && lock.manual_target_norm.length === 2) {
      if (manualDragPointerId === null) {
        manualTargetNorm = {
          x: clamp(Number(lock.manual_target_norm[0] || 0.5), 0, 1),
          y: clamp(Number(lock.manual_target_norm[1] || 0.5), 0, 1),
        };
        updateManualReticlePosition();
      }
    }
    setManualReticleVisibility();
    updateButtonStates(lockEnabled);
    const now = performance.now();
    if (now - lastTelemetryLogAt > 950) {
      appendTacticalLine(
        `TEL role=${state.role || "operator"} mode=${currentLockMode.toUpperCase()} lock=${lockLocked ? "LOCK" : "SCAN"} conf=${Number(lock.confidence || 0).toFixed(2)} pan=${state.current_pan.toFixed(1)} tilt=${state.current_tilt.toFixed(1)}`
      );
      lastTelemetryLogAt = now;
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
    appendTacticalLine("WARN telemetry unavailable");
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
autoResponseValue.textContent = Number(autoResponse.value).toFixed(2);
manualResponseValue.textContent = Number(manualResponse.value).toFixed(2);
updateManualReticlePosition();
window.addEventListener("resize", updateManualReticlePosition);
appendTacticalLine("BOOT command channel online");

refreshState();
setInterval(refreshState, 250);