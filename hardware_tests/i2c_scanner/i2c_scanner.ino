// Development assistance: Codex. Record student authors and IDs before submission.
// Nano RP2040 Connect: A4=SDA, A5=SCL. USB serial monitor 115200.
// Read only; scan once per five seconds. Some onboard devices share this I2C bus.
#include <Wire.h>
void setup() {
  Serial.begin(115200);
  Wire.begin();
}
void loop() {
  Serial.println("--- I2C scan (7-bit addresses) ---");
  int count = 0;
  for (uint8_t address = 1; address < 127; ++address) {
    Wire.beginTransmission(address);
    if (Wire.endTransmission() == 0) {
      Serial.print("Found 0x");
      if (address < 16) Serial.print('0');
      Serial.println(address, HEX);
      ++count;
    }
  }
  Serial.print("Devices: ");
  Serial.println(count);
  delay(5000);
}
