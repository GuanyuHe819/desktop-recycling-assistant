// Board and USB serial check; no external components required.
// Uploading a sketch replaces the sketch currently on the board.
void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
}
void loop() {
  digitalWrite(LED_BUILTIN, HIGH);
  Serial.println("Board test: LED ON");
  delay(1000);
  digitalWrite(LED_BUILTIN, LOW);
  Serial.println("Board test: LED OFF");
  delay(1000);
}
