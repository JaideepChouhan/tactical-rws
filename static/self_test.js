const authStatus = document.getElementById("authStatus");
const cameraStatus = document.getElementById("cameraStatus");
const serialStatus = document.getElementById("serialStatus");
const movementStatus = document.getElementById("movementStatus");
const overallStatus = document.getElementById("overallStatus");
const rawOutput = document.getElementById("rawOutput");
const runTestBtn = document.getElementById("runTestBtn");

let authRequired = false;

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

async function fetchPublicConfig() {
  const response = await fetch("/api/public-config", { cache: "no-store" });
  if (!response.ok) {
    throw new Error("Cannot read public API config");
  }
  return response.json();
}

async function runSelfTest() {
  overallStatus.textContent = "Running...";
  rawOutput.textContent = "";

  try {
    const config = await fetchPublicConfig();
    authRequired = !!config.auth_required;
    authStatus.textContent = authRequired ? "Required" : "Disabled";

    if (authRequired && !getApiKey()) {
      const key = window.prompt("API key required. Enter RWS API key:", "");
      if (key) {
        localStorage.setItem("rwsApiKey", key.trim());
      }
    }

    const response = await fetch("/api/self-test", {
      method: "POST",
      headers: buildHeaders(),
      body: JSON.stringify({}),
    });

    if (response.status === 401) {
      overallStatus.textContent = "Auth failed";
      rawOutput.textContent = "Unauthorized. Set a valid API key and retry.";
      return;
    }

    if (!response.ok) {
      const txt = await response.text();
      overallStatus.textContent = `Failed (${response.status})`;
      rawOutput.textContent = txt;
      return;
    }

    const result = await response.json();

    cameraStatus.textContent = result.camera?.ready
      ? `Ready ${result.camera.resolution?.width || "?"}x${result.camera.resolution?.height || "?"}`
      : (result.camera?.last_error || "Not ready");

    serialStatus.textContent = result.serial_connected
      ? `Connected (${result.serial_port || "detected"})`
      : "Preview mode (no serial)";

    movementStatus.textContent = result.movement_cycle || "n/a";
    overallStatus.textContent = result.ok ? "PASS" : "CHECK REQUIRED";

    rawOutput.textContent = JSON.stringify(result, null, 2);
  } catch (err) {
    overallStatus.textContent = "Error";
    rawOutput.textContent = err.message;
  }
}

runTestBtn.addEventListener("click", runSelfTest);
runSelfTest();
