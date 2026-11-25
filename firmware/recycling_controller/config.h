#pragma once
// Demo wiring individually tested by user; end-to-end behaviour still unverified.
constexpr bool HARDWARE_VERIFIED = true;
constexpr int BIN_COUNT = 5; // use 3 during staged testing, then 5
const int RED_PINS[5] = {2,4,6,8,10};
const int GREEN_PINS[5] = {3,5,7,9,11};
const int XSHUT_PINS[5] = {12,A0,A1,A2,A3};
const uint8_t TOF_ADDRESS[5] = {0x30,0x31,0x32,0x33,0x34};
const char* BIN_LABELS[5] = {"paper","plastic","metal","cardboard","textile"};
// Initial demo values; test against actual background and adjust each channel.
const uint16_t ENTER_MM[5] = {100,100,100,100,100};
const uint16_t CLEAR_MM[5] = {150,150,150,150,150};
constexpr bool LED_ACTIVE_LOW = true;
constexpr bool BUZZER_ENABLED = true;
constexpr bool BUZZER_ACTIVE_LOW = true;
constexpr int BUZZER_PIN = 1; // physical TX pin = Arduino D1 (not D0)
constexpr unsigned BUZZER_FREQUENCY_HZ = 2000; // clearest in the user's buzzer test
constexpr uint8_t OLED_ADDR = 0x3C; // inspect/scan actual module
constexpr bool OLED_ENABLED = true; // assumes SSD1306 128x64
constexpr unsigned LONG_MS = 10000, PEER_TIMEOUT_MS = 6000;
constexpr unsigned FEEDBACK_MS = 1500, FLASH_INTERVAL_MS = 250;
constexpr unsigned CLEAR_SAMPLES = 2;
