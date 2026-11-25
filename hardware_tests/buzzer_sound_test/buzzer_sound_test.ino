// Buzzer test for Arduino Nano RP2040 Connect.
// User's wiring: VCC -> 3V3, GND -> GND, I/O -> board TX (Arduino D1, RP2040 GPIO0).
// USB serial monitor: 115200 baud. Send one command at a time.
// This sketch replaces the sketch running on the board when uploaded.
#include <Arduino.h>

const int BUZZER_PIN = 1;  // Physical TX = Arduino D1; not Arduino D0/RX.
const int RED_PINS[5] = {2, 4, 6, 8, 10};
const int GREEN_PINS[5] = {3, 5, 7, 9, 11};
const unsigned long TEST_MS = 1000;
bool testRunning = false;
bool toneRunning = false;
unsigned long started = 0;

void buzzerOff() {
  if (toneRunning) {
    noTone(BUZZER_PIN);
    toneRunning = false;
  }
  // This module is marked active-low: restore HIGH after a tone.
  digitalWrite(BUZZER_PIN, HIGH);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, HIGH);
  testRunning = false;
}

void printHelp() {
  Serial.println();
  Serial.println(F("=== BUZZER TEST: board TX / Arduino D1 ==="));
  Serial.println(F("Send 0: steady LOW for 1 second"));
  Serial.println(F("Send 1: 1000 Hz for 1 second"));
  Serial.println(F("Send 2: 2000 Hz for 1 second"));
  Serial.println(F("Send 3: 3000 Hz for 1 second"));
  Serial.println(F("Send x: stop; send h: show this menu"));
  Serial.println(F("Listen and compare volume and sound, then report results."));
}

void startLow() {
  buzzerOff();
  Serial.println(F("TEST 0: steady LOW, 1 second"));
  digitalWrite(BUZZER_PIN, LOW);
  started = millis();
  testRunning = true;
}

void startTone(unsigned int frequency) {
  buzzerOff();
  Serial.print(F("TONE: ")); Serial.print(frequency); Serial.println(F(" Hz, 1 second"));
  // Run indefinitely until loop stops it, then explicitly restore inactive HIGH.
  tone(BUZZER_PIN, frequency);
  toneRunning = true;
  started = millis();
  testRunning = true;
}

void setup() {
  // Keep the already connected common-anode LEDs inactive during buzzer tests.
  for (int i = 0; i < 5; ++i) {
    digitalWrite(RED_PINS[i], HIGH); pinMode(RED_PINS[i], OUTPUT);
    digitalWrite(GREEN_PINS[i], HIGH); pinMode(GREEN_PINS[i], OUTPUT);
  }
  buzzerOff();
  Serial.begin(115200);
  // Open the monitor, then send h; no wait for USB serial connection.
}

void loop() {
  if (testRunning && (unsigned long)(millis() - started) >= TEST_MS) {
    buzzerOff();
    Serial.println(F("Test finished; buzzer off."));
  }
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '0') startLow();
    else if (c == '1') startTone(1000);
    else if (c == '2') startTone(2000);
    else if (c == '3') startTone(3000);
    else if (c == 'x' || c == 'X') { buzzerOff(); Serial.println(F("Buzzer off.")); }
    else if (c == 'h' || c == 'H' || c == '?') printHelp();
  }
}
