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
constexpr unsigned long MOVEMENT_TICK_MS  = 16;   // ~60Hz movement update
constexpr unsigned long SERIAL_TIMEOUT_MS = 500;  // serial idle → button mode
constexpr unsigned long PATROL_STEP_MS    = 30;   // patrol sweep tick
constexpr unsigned long FIRE_HOLD_MS      = 250;  // how long trigger stays pulled

// ── Movement speeds (degrees/second) ──────────────────────────────────────────
constexpr float PAN_SPEED_DPS      = 55.0f;
constexpr float TILT_SPEED_DPS     = 35.0f;
constexpr float SENTRY_PAN_DPS     = 22.0f;
constexpr float DIAGONAL_SCALE     = 0.7071f;  // keep diagonal speed balanced

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

// Smooth movement state
unsigned long lastMoveMs     = 0;
float         panResidualDeg = 0.0f;
float         tiltResidualDeg = 0.0f;
float         sentryResidualDeg = 0.0f;

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

// ── Movement helpers ───────────────────────────────────────────────────────────

int consumeWholeDegrees(float& accumulator) {
  int whole = 0;
  if (accumulator >= 1.0f || accumulator <= -1.0f) {
    whole = (int)accumulator;  // truncates toward zero, keeps signed direction
    accumulator -= whole;
  }
  return whole;
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
  }

  lastMoveMs = millis();

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
  unsigned long now = millis();
  unsigned long dtMs = now - lastMoveMs;
  if (dtMs < MOVEMENT_TICK_MS) return;
  lastMoveMs = now;

  int panDir = (btnState[IDX_RIGHT] ? 1 : 0) - (btnState[IDX_LEFT] ? 1 : 0);
  int tiltDir = (btnState[IDX_DOWN] ? 1 : 0) - (btnState[IDX_UP] ? 1 : 0);

  if (panDir == 0 && tiltDir == 0) {
    return;
  }

  float speedScale = (panDir != 0 && tiltDir != 0) ? DIAGONAL_SCALE : 1.0f;
  float dtSec = dtMs / 1000.0f;

  panResidualDeg += panDir * PAN_SPEED_DPS * speedScale * dtSec;
  tiltResidualDeg += tiltDir * TILT_SPEED_DPS * speedScale * dtSec;

  int panStep = consumeWholeDegrees(panResidualDeg);
  int tiltStep = consumeWholeDegrees(tiltResidualDeg);
  if (panStep == 0 && tiltStep == 0) return;

  int newPan = constrain(currentPan + panStep, PAN_MIN, PAN_MAX);
  int newTilt = constrain(currentTilt + tiltStep, TILT_MIN, TILT_MAX);
  if (newPan != currentPan || newTilt != currentTilt) {
    currentPan = newPan;
    currentTilt = newTilt;
    applyServos();
  }
}

// ── Patrol sweep (PATROL mode) ────────────────────────────────────────────────

void handlePatrol() {
  unsigned long now = millis();
  if (now - patrolLastMs >= PATROL_STEP_MS) {
    patrolLastMs = now;
    currentPan  += patrolDir;
    if (currentPan >= PAN_MAX) { currentPan = PAN_MAX; patrolDir = -1; }
    if (currentPan <= PAN_MIN) { currentPan = PAN_MIN; patrolDir =  1; }
    servoPan.write(currentPan);
  }

  // Tilt remains smooth manual while pan patrols.
  unsigned long dtMs = now - lastMoveMs;
  if (dtMs >= MOVEMENT_TICK_MS) {
    lastMoveMs = now;
    int tiltDir = (btnState[IDX_DOWN] ? 1 : 0) - (btnState[IDX_UP] ? 1 : 0);
    if (tiltDir != 0) {
      float dtSec = dtMs / 1000.0f;
      tiltResidualDeg += tiltDir * TILT_SPEED_DPS * dtSec;
      int tiltStep = consumeWholeDegrees(tiltResidualDeg);
      if (tiltStep != 0) {
        int newTilt = constrain(currentTilt + tiltStep, TILT_MIN, TILT_MAX);
        if (newTilt != currentTilt) {
          currentTilt = newTilt;
          servoTilt.write(currentTilt);
        }
      }
    }
  }
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
      {
        unsigned long now = millis();
        unsigned long dtMs = now - lastMoveMs;
        if (dtMs >= MOVEMENT_TICK_MS) {
          lastMoveMs = now;
          int panDir = (btnState[IDX_RIGHT] ? 1 : 0) - (btnState[IDX_LEFT] ? 1 : 0);
          if (panDir != 0) {
            float dtSec = dtMs / 1000.0f;
            sentryResidualDeg += panDir * SENTRY_PAN_DPS * dtSec;
            int panStep = consumeWholeDegrees(sentryResidualDeg);
            if (panStep != 0) {
              int newPan = constrain(currentPan + panStep, PAN_MIN, PAN_MAX);
              if (newPan != currentPan) {
                currentPan = newPan;
                servoPan.write(currentPan);
              }
            }
          }
        }
      }
      break;
  }
}
