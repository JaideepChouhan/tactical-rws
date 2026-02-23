#include <Servo.h>

Servo servoPan;
Servo servoTilt;
Servo servoTrigger;

String receivedData;
int commaIndex;
int secondCommaIndex;
int panAngle;
int tiltAngle;
int triggerAngle;

void setup() {
  Serial.begin(9600);
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

    commaIndex = receivedData.indexOf(',');
    secondCommaIndex = receivedData.indexOf(',', commaIndex + 1);
    
    if (commaIndex > 0 && secondCommaIndex > 0) {
      panAngle = receivedData.substring(0, commaIndex).toInt();
      tiltAngle = receivedData.substring(commaIndex + 1, secondCommaIndex).toInt();
      triggerAngle = receivedData.substring(secondCommaIndex + 1).toInt();

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
