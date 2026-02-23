#include<Servo.h>

Servo sr1, sr2, sr3;


void setup()
{
  sr1.attach(5);
  sr2.attach(6);
  sr3.attach(7);
}

void loop()
{
  sr1.write(90);
  sr2.write(90);
  sr3.write(90);
  delay(100);
}
