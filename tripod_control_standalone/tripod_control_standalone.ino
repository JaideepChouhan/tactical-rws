// =============================================================================
//  AIR0214 Smart RWS – Standalone 6-Button Control Firmware
//  Servo pins  :  Pan = 5 | Tilt = 6 | Trigger = 9   (unchanged)
//  Button pins :  UP=2  DOWN=3  LEFT=4  RIGHT=7  TRIGGER=8  MODE=10
//
//  Wiring (no resistors needed – uses internal pull-ups):
//    Each button → one leg to the assigned pin, other leg to GND.
//
//  Three operating MODES (cycle with MODE button):
//    0 – MANUAL  : D-pad moves servos; trigger fires on press/release
//    1 – PATROL  : Pan auto-sweeps left↔right; tilt & trigger still manual
//    2 – SENTRY  : Servos lock to centre; trigger still fires; tap MODE again
//                  to return to MANUAL
//
//  Serial compatibility: if a valid "pan,tilt,trigger\n" packet arrives from
//  the laptop, it takes over immediately. Button control resumes the moment
//  serial goes quiet (> SERIAL_TIMEOUT_MS with no new packet).
// =============================================================================

#include <Servo.h>

// ── Servo objects ──────────────────────────────────────────────────────────────
Servo servoPan;
Servo servoTilt;
Servo servoTrigger;

// ── Servo limits (must match web_control_server.py) ───────────────────────────
constexpr int PAN_MIN       = 0;
constexpr int PAN_MAX       = 180;
constexpr int PAN_CENTER    = 90;

constexpr int TILT_MIN      = 70;
constexpr int TILT_MAX      = 110;
constexpr int TILT_CENTER   = 90;

constexpr int TRIG_SAFE     = 45;
constexpr int TRIG_FIRE     = 135;

// ── Button pin assignments ─────────────────────────────────────────────────────
constexpr uint8_t BTN_UP      = 2;
constexpr uint8_t BTN_DOWN    = 3;
constexpr uint8_t BTN_LEFT    = 4;
constexpr uint8_t BTN_RIGHT   = 7;
constexpr uint8_t BTN_TRIGGER = 8;
constexpr uint8_t BTN_MODE    = 10;

constexpr uint8_t BTN_COUNT   = 6;
const uint8_t BTN_PINS[BTN_COUNT] = {
  BTN_UP, BTN_DOWN, BTN_LEFT, BTN_RIGHT, BTN_TRIGGER, BTN_MODE
};

// ── Timing constants ───────────────────────────────────────────────────────────
constexpr unsigned long DEBOUNCE_MS       = 25;   // debounce window
constexpr unsigned long HOLD_DELAY_MS     = 300;  // pause before repeat fires
constexpr unsigned long REPEAT_INTERVAL_MS = 60;  // repeat rate while held
constexpr unsigned long SERIAL_TIMEOUT_MS = 500;  // serial idle → button mode
constexpr unsigned long PATROL_STEP_MS    = 30;   // patrol sweep tick
constexpr unsigned long FIRE_HOLD_MS      = 250;  // how long trigger stays pulled

// ── Movement step sizes ────────────────────────────────────────────────────────
constexpr int PAN_STEP  = 2;   // degrees per tick
constexpr int TILT_STEP = 1;

// ── Mode definitions ───────────────────────────────────────────────────────────
enum Mode : uint8_t {
  MODE_MANUAL = 0,
  MODE_PATROL = 1,
  MODE_SENTRY = 2,
  MODE_COUNT  = 3
};
const char* MODE_NAMES[MODE_COUNT] = { "MANUAL", "PATROL", "SENTRY" };

// ── State variables ────────────────────────────────────────────────────────────
int currentPan   = PAN_CENTER;
int currentTilt  = TILT_CENTER;
int lastTrigger  = TRIG_SAFE;

Mode currentMode = MODE_MANUAL;

// Button debounce state
bool     btnState[BTN_COUNT];        // current debounced state (true = pressed)
bool     btnRaw[BTN_COUNT];          // raw read last loop
unsigned long btnLastChange[BTN_COUNT];  // millis of last raw edge
unsigned long btnHoldStart[BTN_COUNT];   // millis when debounced press began
bool     btnHoldFired[BTN_COUNT];    // has hold-repeat started?
unsigned long btnLastRepeat[BTN_COUNT];  // millis of last repeat tick

// Serial
String        serialBuf      = "";
unsigned long lastSerialMs   = 0;
bool          serialActive   = false;

// Trigger fire timing
bool          triggerArmed   = false;
unsigned long triggerFireMs  = 0;

// Patrol state
int           patrolDir      = 1;          // +1 right, -1 left
unsigned long patrolLastMs   = 0;

// ── Helpers ────────────────────────────────────────────────────────────────────

void applyServos() {
  servoPan.write(currentPan);
  servoTilt.write(currentTilt);
}

void applyTrigger(int angle) {
  if (angle != lastTrigger) {
    servoTrigger.write(angle);
    lastTrigger = angle;
  }
}

void centerAll() {
  currentPan  = PAN_CENTER;
  currentTilt = TILT_CENTER;
  applyServos();
  applyTrigger(TRIG_SAFE);
}

// Print current mode over Serial for laptop monitoring
void reportMode() {
  Serial.print(F("MODE:"));
  Serial.println(MODE_NAMES[currentMode]);
}

// ── Button helpers ─────────────────────────────────────────────────────────────

// Returns true on the first pressed edge (debounced)
bool justPressed(uint8_t idx) {
  return btnState[idx] && !btnHoldFired[idx] &&
         (millis() - btnHoldStart[idx] < HOLD_DELAY_MS) &&
         (millis() - btnHoldStart[idx] >= DEBOUNCE_MS);
}

// Returns true every REPEAT_INTERVAL_MS while button is held (after HOLD_DELAY_MS)
bool heldRepeat(uint8_t idx) {
  if (!btnState[idx]) return false;
  unsigned long now = millis();
  unsigned long heldFor = now - btnHoldStart[idx];
  if (heldFor < HOLD_DELAY_MS) return false;
  if (!btnHoldFired[idx]) {
    btnHoldFired[idx] = true;
    btnLastRepeat[idx] = now;
    return true;  // first repeat tick
  }
  if (now - btnLastRepeat[idx] >= REPEAT_INTERVAL_MS) {
    btnLastRepeat[idx] = now;
    return true;
  }
  return false;
}

// ── Setup ──────────────────────────────────────────────────────────────────────

void setup() {
  Serial.begin(9600);
  Serial.setTimeout(10);

  servoPan.attach(5);
  servoTilt.attach(6);
  servoTrigger.attach(9);

  centerAll();

  // Initialise button pins with internal pull-ups
  for (uint8_t i = 0; i < BTN_COUNT; i++) {
    pinMode(BTN_PINS[i], INPUT_PULLUP);
    btnState[i]      = false;
    btnRaw[i]        = true;   // HIGH = not pressed with pull-up
    btnLastChange[i] = 0;
    btnHoldStart[i]  = 0;
    btnHoldFired[i]  = false;
    btnLastRepeat[i] = 0;
  }

  Serial.println(F("AIR0214 STANDALONE READY"));
  reportMode();
}

// ── Serial handler ─────────────────────────────────────────────────────────────
// Mirrors original tripod_control.ino parsing. Overwrites servo state if valid.

void handleSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n') {
      serialBuf.trim();
      int pan, tilt, trig;
      if (sscanf(serialBuf.c_str(), "%d,%d,%d", &pan, &tilt, &trig) == 3) {
        currentPan  = constrain(pan,  PAN_MIN,  PAN_MAX);
        currentTilt = constrain(tilt, TILT_MIN, TILT_MAX);
        applyServos();
        applyTrigger(constrain(trig, TRIG_SAFE, TRIG_FIRE));
        lastSerialMs  = millis();
        serialActive  = true;
      }
      serialBuf = "";
    } else {
      if (serialBuf.length() < 32) serialBuf += c;
    }
  }

  // If serial has gone quiet, hand back control to buttons
  if (serialActive && (millis() - lastSerialMs > SERIAL_TIMEOUT_MS)) {
    serialActive = false;
    Serial.println(F("SERIAL_IDLE:BUTTON_CTRL_ACTIVE"));
  }
}

// ── Debounce update ────────────────────────────────────────────────────────────

void updateButtons() {
  unsigned long now = millis();
  for (uint8_t i = 0; i < BTN_COUNT; i++) {
    bool raw = (digitalRead(BTN_PINS[i]) == LOW);  // LOW = pressed (pull-up)
    if (raw != btnRaw[i]) {
      btnRaw[i]        = raw;
      btnLastChange[i] = now;
    }
    if ((now - btnLastChange[i]) >= DEBOUNCE_MS) {
      if (raw && !btnState[i]) {
        // Freshly pressed
        btnState[i]      = true;
        btnHoldStart[i]  = now;
        btnHoldFired[i]  = false;
      } else if (!raw && btnState[i]) {
        // Released
        btnState[i] = false;
      }
    }
  }
}

// ── Index helpers (so switch-case can use named constants) ─────────────────────
constexpr uint8_t IDX_UP      = 0;
constexpr uint8_t IDX_DOWN    = 1;
constexpr uint8_t IDX_LEFT    = 2;
constexpr uint8_t IDX_RIGHT   = 3;
constexpr uint8_t IDX_TRIGGER = 4;
constexpr uint8_t IDX_MODE    = 5;

// ── Mode button (edge on press only, no repeat) ────────────────────────────────

void handleModeButton() {
  // Detect single press edge: state is true but hold-repeat not yet started
  static bool modeWasPressed = false;
  bool modeNow = btnState[IDX_MODE];
  if (modeNow && !modeWasPressed) {
    currentMode = (Mode)((currentMode + 1) % MODE_COUNT);
    reportMode();
    if (currentMode == MODE_SENTRY) {
      centerAll();  // Snap to centre when entering sentry
    }
  }
  modeWasPressed = modeNow;
}

// ── Trigger button ─────────────────────────────────────────────────────────────

void handleTriggerButton() {
  unsigned long now = millis();

  // Start a fire pulse on press
  if (btnState[IDX_TRIGGER] && !triggerArmed) {
    triggerArmed  = true;
    triggerFireMs = now;
    applyTrigger(TRIG_FIRE);
    Serial.println(F("EVENT:FIRE"));
  }

  // Release trigger after FIRE_HOLD_MS even if button is still held
  if (triggerArmed && (now - triggerFireMs >= FIRE_HOLD_MS)) {
    triggerArmed = false;
    applyTrigger(TRIG_SAFE);
  }
}

// ── D-pad movement (MANUAL mode) ──────────────────────────────────────────────

void handleDpad() {
  bool moved = false;

  // UP  → tilt up (decrease angle toward TILT_MIN)
  if (justPressed(IDX_UP) || heldRepeat(IDX_UP)) {
    currentTilt = constrain(currentTilt - TILT_STEP, TILT_MIN, TILT_MAX);
    moved = true;
  }
  // DOWN → tilt down (increase angle toward TILT_MAX)
  if (justPressed(IDX_DOWN) || heldRepeat(IDX_DOWN)) {
    currentTilt = constrain(currentTilt + TILT_STEP, TILT_MIN, TILT_MAX);
    moved = true;
  }
  // LEFT  → pan left (decrease pan angle)
  if (justPressed(IDX_LEFT) || heldRepeat(IDX_LEFT)) {
    currentPan = constrain(currentPan - PAN_STEP, PAN_MIN, PAN_MAX);
    moved = true;
  }
  // RIGHT → pan right (increase pan angle)
  if (justPressed(IDX_RIGHT) || heldRepeat(IDX_RIGHT)) {
    currentPan = constrain(currentPan + PAN_STEP, PAN_MIN, PAN_MAX);
    moved = true;
  }

  if (moved) applyServos();
}

// ── Patrol sweep (PATROL mode) ────────────────────────────────────────────────

void handlePatrol() {
  unsigned long now = millis();
  if (now - patrolLastMs >= PATROL_STEP_MS) {
    patrolLastMs = now;
    currentPan  += patrolDir * PAN_STEP;
    if (currentPan >= PAN_MAX) { currentPan = PAN_MAX; patrolDir = -1; }
    if (currentPan <= PAN_MIN) { currentPan = PAN_MIN; patrolDir =  1; }
    servoPan.write(currentPan);
  }
  // Tilt is still manually adjustable in patrol mode
  bool moved = false;
  if (justPressed(IDX_UP)   || heldRepeat(IDX_UP))   { currentTilt = constrain(currentTilt - TILT_STEP, TILT_MIN, TILT_MAX); moved = true; }
  if (justPressed(IDX_DOWN) || heldRepeat(IDX_DOWN))  { currentTilt = constrain(currentTilt + TILT_STEP, TILT_MIN, TILT_MAX); moved = true; }
  if (moved) servoTilt.write(currentTilt);
}

// ── Main loop ──────────────────────────────────────────────────────────────────

void loop() {
  handleSerial();   // always listen for laptop commands first

  updateButtons();  // debounce all 6 buttons

  handleModeButton();    // mode cycling (works in all modes)
  handleTriggerButton(); // trigger pulse (works in all modes)

  if (serialActive) return;  // laptop is in control – skip button movement

  switch (currentMode) {
    case MODE_MANUAL:
      handleDpad();
      break;

    case MODE_PATROL:
      handlePatrol();
      break;

    case MODE_SENTRY:
      // Servos are already centred; only trigger is active.
      // Left/Right can still nudge pan for micro-corrections.
      if (justPressed(IDX_LEFT)  || heldRepeat(IDX_LEFT))  { currentPan = constrain(currentPan - PAN_STEP, PAN_MIN, PAN_MAX); servoPan.write(currentPan); }
      if (justPressed(IDX_RIGHT) || heldRepeat(IDX_RIGHT))  { currentPan = constrain(currentPan + PAN_STEP, PAN_MIN, PAN_MAX); servoPan.write(currentPan); }
      break;
  }
}
