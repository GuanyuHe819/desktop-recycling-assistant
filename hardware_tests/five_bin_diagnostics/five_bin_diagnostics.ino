// Five-bin hardware diagnostics, assisted by Codex.
// Add the actual students' full names and student IDs before course submission.
// Board: Arduino Nano RP2040 Connect; USB serial monitor: 115200 baud.
// Required libraries: Adafruit VL53L0X, Adafruit SSD1306, Adafruit GFX, Adafruit BusIO.
// One lamp at a time; does NOT run AI, MQTT, automatic sorting or acceptance testing.
#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_VL53L0X.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <stdio.h>
#include <string.h>
#include <ctype.h>

const uint8_t N_BINS = 5;
const int RED_PINS[N_BINS] = {2, 4, 6, 8, 10};
const int GREEN_PINS[N_BINS] = {3, 5, 7, 9, 11};
const int XSHUT_PINS[N_BINS] = {12, A0, A1, A2, A3};
const uint8_t TOF_ADDRESS[N_BINS] = {0x30, 0x31, 0x32, 0x33, 0x34};
// Correction: the physical TX pin is Arduino D1, and RX is D0 on this board.
const int BUZZER_PIN = 1;  // physical pin TX, not RX
const uint8_t OLED_ADDRESS = 0x3C; // candidate from course exercise; scan first

Adafruit_VL53L0X tof[N_BINS];
Adafruit_SSD1306 display(128, 64, &Wire, -1);
bool tofReady[N_BINS] = {false, false, false, false, false};
bool displayReady = false;
bool lightRunning = false;
bool buzzerRunning = false;
unsigned long lightSince = 0;
unsigned long buzzerSince = 0;
const unsigned long LIGHT_MS = 2000;
const unsigned long BUZZER_MS = 250;
char lineBuffer[64];
uint8_t lineLen = 0;
bool lineOverflow = false;

void printHelp() {
  Serial.println();
  Serial.println(F("=== FIVE BIN DIAGNOSTICS ==="));
  Serial.println(F("Commands: type, then Send (Newline or Both NL & CR)."));
  Serial.println(F("help       : show this menu"));
  Serial.println(F("scan       : scan shared I2C bus"));
  Serial.println(F("oled       : show test message on 0x3C OLED"));
  Serial.println(F("r1..r5     : red lamp 1..5, 2 seconds"));
  Serial.println(F("g1..g5     : green lamp 1..5, 2 seconds"));
  Serial.println(F("off        : turn lamps and buzzer off"));
  Serial.println(F("beep       : low-trigger buzzer for 0.25 seconds"));
  Serial.println(F("init       : reset and assign 5 ToF addresses"));
  Serial.println(F("t1..t5     : read one initialized ToF 10 times"));
  Serial.println(F("ta         : read all five initialized ToFs once"));
  Serial.println(F("status     : show software initialization state"));
  Serial.println(F("LEDs assumed common anode + separate limit resistors."));
  Serial.println(F("Buzzer physical TX = D1. No Wi-Fi or MQTT is used."));
}
void outputsOff() {
  for (uint8_t i=0; i<N_BINS; i++) {
    digitalWrite(RED_PINS[i], HIGH);
    digitalWrite(GREEN_PINS[i], HIGH);
  }
  digitalWrite(BUZZER_PIN, HIGH);
  lightRunning = false;
  buzzerRunning = false;
}
void shutSensors() {
  for (uint8_t i=0; i<N_BINS; i++) {
    digitalWrite(XSHUT_PINS[i], LOW);
    pinMode(XSHUT_PINS[i], OUTPUT);
    tofReady[i] = false;
  }
}
void scanBus() {
  Serial.println(F("I2C scan, 7-bit addresses:"));
  uint8_t count=0;
  for (uint8_t address=1; address<127; address++) {
    Wire.beginTransmission(address);
    if (Wire.endTransmission()==0) {
      Serial.print(F("  0x"));
      if (address<16) Serial.print('0');
      Serial.println(address, HEX);
      count++;
    }
  }
  Serial.print(F("Devices found: ")); Serial.println(count);
  Serial.println(F("NOTE: onboard I2C devices may also appear."));
  Serial.println(F("Before init all 5 XSHUT pins should be held low."));
}
void testOled() {
  Wire.beginTransmission(OLED_ADDRESS);
  if (Wire.endTransmission()!=0) {
    Serial.println(F("OLED: no response at 0x3C. Run scan and check wiring/address."));
    return;
  }
  if (!displayReady) {
    displayReady = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS);
  }
  if (!displayReady) { Serial.println(F("OLED: initialization failed.")); return; }
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0,0);
  display.println(F("RECYCLING PROJECT"));
  display.println(F("OLED TEST"));
  display.println(F("If you can read this,"));
  display.println(F("display output works."));
  display.display();
  Serial.println(F("OLED: command sent. Check screen text yourself."));
}
void testLamp(uint8_t idx, bool red) {
  outputsOff();
  digitalWrite(red ? RED_PINS[idx] : GREEN_PINS[idx], LOW);
  lightSince=millis(); lightRunning=true;
  Serial.print(F("Lamp "));Serial.print(idx+1);
  Serial.println(red ? F(" RED on for 2 s") : F(" GREEN on for 2 s"));
}
void testBuzzer() {
  outputsOff();
  digitalWrite(BUZZER_PIN, LOW);
  buzzerSince=millis(); buzzerRunning=true;
  Serial.println(F("Buzzer: D1/TX LOW for 0.25 s. Listen for sound."));
  Serial.println(F("No sound alone does not distinguish wiring vs passive module."));
}
void initializeTofs() {
  outputsOff(); shutSensors(); delay(50);
  Serial.println(F("Reset all XSHUT. Starting sensors one at a time..."));
  for (uint8_t i=0; i<N_BINS; i++) {
    // Release XSHUT, relying on a compatible module's onboard pull-up.
    // Never drive XSHUT HIGH from the Nano. Verify actual breakout's circuit.
    pinMode(XSHUT_PINS[i], INPUT);
    delay(40);
    Serial.print(F("Sensor ")); Serial.print(i+1); Serial.print(F(" -> 0x")); Serial.print(TOF_ADDRESS[i],HEX);
    if (!tof[i].begin(TOF_ADDRESS[i], false, &Wire)) {
      Serial.println(F(" FAILED. Stopping and shutting down all sensors."));
      shutSensors();
      Serial.println(F("Check wiring, XSHUT pull-up, address conflicts; then retry init."));
      return;
    }
    tofReady[i]=true;
    Serial.println(F(" OK (initialization only, not a physical detection pass)"));
  }
  Serial.println(F("All 5 initialized. Run scan, then t1..t5."));
}
void readTof(uint8_t idx) {
  Serial.print(F("S")); Serial.print(idx+1); Serial.print(F(": "));
  if (!tofReady[idx]) {Serial.println(F("not initialized; send init first"));return;}
  VL53L0X_RangingMeasurementData_t measurement = {};
  VL53L0X_Error error = tof[idx].rangingTest(&measurement, false);
  Serial.print(F("api=")); Serial.print((int)error);
  Serial.print(F(" status=")); Serial.print(measurement.RangeStatus);
  if (error==VL53L0X_ERROR_NONE && measurement.RangeStatus==0) {
    Serial.print(F(" distance=")); Serial.print(measurement.RangeMilliMeter); Serial.println(F(" mm"));
  } else {
    Serial.println(F(" INVALID (not an object event)"));
  }
}
void showStatus() {
  for (uint8_t i=0;i<N_BINS;i++) {
    Serial.print(F("Sensor ")); Serial.print(i+1); Serial.print(F(": "));
    Serial.println(tofReady[i] ? F("INITIALIZED") : F("NOT INITIALIZED"));
  }
  Serial.print(F("OLED initialized: ")); Serial.println(displayReady ? F("yes") : F("no"));
  Serial.println(F("Initialization is not a substitute for real sensor/light tests."));
}
void command(char *s) {
  while (*s==' ' || *s=='\t') s++;
  for (char *p=s; *p; ++p) *p=(char)tolower((unsigned char)*p);
  size_t len=strlen(s);
  while (len && (s[len-1]==' ' || s[len-1]=='\t')) s[--len]='\0';
  if (!len) return;
  // Cancel any active output before a command that might block on I2C.
  outputsOff();
  if (!strcmp(s,"help")) printHelp();
  else if (!strcmp(s,"off")){outputsOff();Serial.println(F("Outputs off."));}
  else if (!strcmp(s,"scan")) scanBus();
  else if (!strcmp(s,"oled")) testOled();
  else if (!strcmp(s,"beep")) testBuzzer();
  else if (!strcmp(s,"init")) initializeTofs();
  else if (!strcmp(s,"status")) showStatus();
  else if (!strcmp(s,"ta")) {for(uint8_t i=0;i<N_BINS;i++)readTof(i);}
  else if (len==2 && s[1]>='1' && s[1]<='5' && (s[0]=='r'||s[0]=='g')) testLamp((uint8_t)(s[1]-'1'),s[0]=='r');
  else if (len==2 && s[1]>='1' && s[1]<='5' && s[0]=='t') {
    uint8_t i=(uint8_t)(s[1]-'1');
    for(int k=0;k<10;k++){readTof(i);delay(200);}
  } else Serial.println(F("Unknown command; type help."));
}
void setup() {
  // Set an inactive output latch before making pins outputs. Only correct for
  // the tested common-anode/active-low wiring; applies after this sketch starts.
  for(uint8_t i=0;i<N_BINS;i++) {
    digitalWrite(RED_PINS[i],HIGH); pinMode(RED_PINS[i],OUTPUT);
    digitalWrite(GREEN_PINS[i],HIGH); pinMode(GREEN_PINS[i],OUTPUT);
  }
  digitalWrite(BUZZER_PIN,HIGH); pinMode(BUZZER_PIN,OUTPUT);
  shutSensors();
  Serial.begin(115200);
  Wire.begin();
  // No blocking wait for Serial. Send help after opening the monitor.
}
void loop() {
  while (Serial.available()) {
    char ch=(char)Serial.read();
    if (ch=='\n' || ch=='\r') {
      if (lineOverflow) Serial.println(F("Command too long."));
      else if (lineLen) {lineBuffer[lineLen]='\0';command(lineBuffer);}
      lineLen=0;lineOverflow=false;
    } else if (!lineOverflow && lineLen<sizeof(lineBuffer)-1) lineBuffer[lineLen++]=ch;
    else lineOverflow=true;
  }
  if (lightRunning && (unsigned long)(millis()-lightSince)>=LIGHT_MS) {
    outputsOff(); Serial.println(F("Lamp test complete; outputs off."));
  }
  if (buzzerRunning && (unsigned long)(millis()-buzzerSince)>=BUZZER_MS) {
    digitalWrite(BUZZER_PIN,HIGH);buzzerRunning=false;Serial.println(F("Buzzer test complete."));
  }
}
