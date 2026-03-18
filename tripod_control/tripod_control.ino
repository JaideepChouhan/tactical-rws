#include <Servo.h>

Servo servoPan;
Servo servoTilt;
Servo servoTrigger;

String receivedData;
int panAngle;
int tiltAngle;
int triggerAngle;

void setup() {
  Serial.begin(9600);
  Serial.setTimeout(10);
  servoPan.attach(5);
  servoTilt.attach(6);
  servoTrigger.attach(9);
  
  servoPan.write(90);
  servoTilt.write(90);
  servoTrigger.write(45);
}

void loop() {
  if (Serial.available() > 0) {
    receivedData = Serial.readStringUntil('\n');
    receivedData.trim();

    // Faster and safer parser for "pan,tilt,trigger" packets.
    if (sscanf(receivedData.c_str(), "%d,%d,%d", &panAngle, &tiltAngle, &triggerAngle) == 3) {

      panAngle = constrain(panAngle, 0, 180);
      tiltAngle = constrain(tiltAngle, 70, 110);
      triggerAngle = constrain(triggerAngle, 45, 135);

      servoPan.write(panAngle);
      servoTilt.write(tiltAngle);
      
      // Only update trigger if changed (reduces jitter)
      static int lastTrigger = -1;
      if (triggerAngle != lastTrigger) {
        servoTrigger.write(triggerAngle);
        lastTrigger = triggerAngle;
      }
    }
  }
}
