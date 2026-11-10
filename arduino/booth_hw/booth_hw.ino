// Smart Photo Booth - Arduino Uno hardware controller
// Connected to Node 1 (RPi 400) by USB serial, 115200 baud, one command per line.
//
// Libraries (Arduino IDE > Library Manager):
//   - "LiquidCrystal I2C" by Frank de Brabander
//   - "LedControl" by Eberhard Fahle
//
// Commands from RPi 1 (each answered with "ACK:<command>"):
//   PING                 -> PONG
//   STATE:SLEEP          screen off, all LEDs off
//   STATE:IDLE           welcome text, smile icon, green LED
//   COUNT:<n>            countdown step (3 = red, 2 = yellow, 1 = green), digit on matrix, beep
//   GAZE:WARN / GAZE:OK  start / stop red LED blinking + "Look at the lens"
//   SHUTTER              shutter sound + flash LED, "Cheese!"
//   PROCESSING           "Processing..." + heart icon
//   DONE                 "Scan the QR!" + melody
//   ERROR:<text>         error text, red LED, low beep
//   LCD:<line1>|<line2>  show custom text (ASCII only, 16 chars per line)
//   TEST                 self-test of every part
// Messages to RPi 1:
//   READY                after boot
//   BTN:START            start button pressed (debounced, max once per second)
//   ERR:UNKNOWN:<cmd>    command not recognised
//
// Note: HD44780 LCDs cannot show Korean, so texts are in English.

#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <LedControl.h>

// ---------- Pins ----------
const uint8_t PIN_BUTTON     = 2;   // other leg to GND (internal pull-up)
const uint8_t PIN_LED_RED    = 5;   // via 220 ohm
const uint8_t PIN_LED_YELLOW = 6;   // via 220 ohm
const uint8_t PIN_LED_GREEN  = 7;   // via 220 ohm
const uint8_t PIN_FLASH      = 8;   // white LED via 100-220 ohm
const uint8_t PIN_BUZZER     = 9;   // passive buzzer
const uint8_t PIN_MATRIX_CS  = 10;  // MAX7219 CS
const uint8_t PIN_MATRIX_DIN = 11;  // MAX7219 DIN
const uint8_t PIN_MATRIX_CLK = 13;  // MAX7219 CLK
// LCD I2C backpack: SDA -> A4, SCL -> A5

const uint8_t LCD_ADDR = 0x27;      // try 0x3F if the screen stays blank
const long BAUD = 115200;

LiquidCrystal_I2C lcd(LCD_ADDR, 16, 2);
LedControl matrix(PIN_MATRIX_DIN, PIN_MATRIX_CLK, PIN_MATRIX_CS, 1);

// ---------- 8x8 icons (one byte per row, bit 7 = left column) ----------
const byte ICON_SMILE[8]  = {0x3C, 0x42, 0xA5, 0x81, 0xA5, 0x99, 0x42, 0x3C};
const byte ICON_HEART[8]  = {0x00, 0x66, 0xFF, 0xFF, 0x7E, 0x3C, 0x18, 0x00};
const byte ICON_EYE[8]    = {0x00, 0x3C, 0x42, 0x99, 0x99, 0x42, 0x3C, 0x00};
const byte ICON_X[8]      = {0x81, 0x42, 0x24, 0x18, 0x18, 0x24, 0x42, 0x81};
const byte ICON_CAMERA[8] = {0x18, 0x7E, 0xFF, 0xE7, 0xDB, 0xE7, 0xFF, 0x00};
const byte ICON_1[8]      = {0x18, 0x38, 0x18, 0x18, 0x18, 0x18, 0x3C, 0x00};
const byte ICON_2[8]      = {0x3C, 0x42, 0x02, 0x0C, 0x30, 0x40, 0x7E, 0x00};
const byte ICON_3[8]      = {0x3C, 0x42, 0x02, 0x1C, 0x02, 0x42, 0x3C, 0x00};

// ---------- State ----------
char rxBuf[64];
uint8_t rxLen = 0;

bool gazeBlink = false;
bool blinkOn = false;
unsigned long lastBlink = 0;

int lastReading = HIGH;
int buttonState = HIGH;
unsigned long lastChange = 0;
unsigned long lastPress = 0;

// ---------- Helpers ----------
void showIcon(const byte icon[8]) {
  for (uint8_t row = 0; row < 8; row++) {
    matrix.setRow(0, row, icon[row]);
  }
}

void setLeds(bool red, bool yellow, bool green) {
  digitalWrite(PIN_LED_RED, red ? HIGH : LOW);
  digitalWrite(PIN_LED_YELLOW, yellow ? HIGH : LOW);
  digitalWrite(PIN_LED_GREEN, green ? HIGH : LOW);
}

void lcdShow(const char *line1, const char *line2) {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print(line1);
  lcd.setCursor(0, 1);
  lcd.print(line2);
}

void shutter() {
  digitalWrite(PIN_FLASH, HIGH);
  tone(PIN_BUZZER, 2200, 40);
  delay(60);
  tone(PIN_BUZZER, 1300, 70);
  delay(110);
  digitalWrite(PIN_FLASH, LOW);
}

void doneMelody() {
  const int notes[] = {1047, 1319, 1568, 2093};
  for (uint8_t i = 0; i < 4; i++) {
    tone(PIN_BUZZER, notes[i], 90);
    delay(110);
  }
}

void stopGazeBlink() {
  gazeBlink = false;
  blinkOn = false;
  digitalWrite(PIN_LED_RED, LOW);
}

// ---------- States ----------
void stateSleep() {
  stopGazeBlink();
  setLeds(false, false, false);
  matrix.clearDisplay(0);
  lcd.clear();
  lcd.noBacklight();
}

void stateIdle() {
  stopGazeBlink();
  lcd.backlight();
  lcdShow("Smart PhotoBooth", "Show your palm!");
  showIcon(ICON_SMILE);
  setLeds(false, false, true);
}

void countdown(int n) {
  stopGazeBlink();
  char line1[17];
  snprintf(line1, sizeof(line1), "      %d ...", n);
  lcdShow(line1, "Get ready!");
  setLeds(n >= 3, n == 2, n <= 1);
  if (n == 3) showIcon(ICON_3);
  else if (n == 2) showIcon(ICON_2);
  else if (n == 1) showIcon(ICON_1);
  else showIcon(ICON_SMILE);
  tone(PIN_BUZZER, 1000, 80);
}

void gazeWarn() {
  lcdShow("Look at the lens", "   ( O    O )");
  showIcon(ICON_EYE);
  setLeds(false, false, false);
  gazeBlink = true;
  lastBlink = 0;
}

void gazeOk() {
  stopGazeBlink();
  lcdShow("Good! Keep it", "   up...");
  showIcon(ICON_SMILE);
}

void doShutter() {
  stopGazeBlink();
  lcdShow("   Cheese!", "");
  showIcon(ICON_CAMERA);
  shutter();
  setLeds(false, false, false);
}

void processing() {
  lcdShow("Processing...", "Please wait");
  showIcon(ICON_HEART);
  setLeds(false, true, false);
}

void done() {
  lcdShow("Done!", "Scan the QR!");
  showIcon(ICON_HEART);
  setLeds(false, false, true);
  doneMelody();
}

void error(const char *text) {
  stopGazeBlink();
  lcdShow("Error", text);
  showIcon(ICON_X);
  setLeds(true, false, false);
  tone(PIN_BUZZER, 300, 400);
}

void customLcd(char *arg) {
  char *sep = strchr(arg, '|');
  if (sep != NULL) {
    *sep = '\0';
    lcdShow(arg, sep + 1);
  } else {
    lcdShow(arg, "");
  }
}

void selfTest() {
  lcd.backlight();
  lcdShow("Self test", "LEDs...");
  setLeds(true, false, false);  delay(400);
  setLeds(false, true, false);  delay(400);
  setLeds(false, false, true);  delay(400);
  setLeds(false, false, false);
  lcdShow("Self test", "Flash + buzzer");
  shutter();
  delay(400);
  lcdShow("Self test", "Matrix icons");
  const byte *icons[] = {ICON_3, ICON_2, ICON_1, ICON_SMILE, ICON_HEART, ICON_EYE, ICON_CAMERA, ICON_X};
  for (uint8_t i = 0; i < 8; i++) {
    showIcon(icons[i]);
    delay(350);
  }
  lcdShow("Self test", "Press button...");
  showIcon(ICON_SMILE);
}

// ---------- Command parser ----------
void handleCommand(char *cmd) {
  char *arg = strchr(cmd, ':');
  if (arg != NULL) {
    *arg = '\0';
    arg++;
  }

  bool ok = true;
  if (strcmp(cmd, "PING") == 0) {
    Serial.println(F("PONG"));
    return;
  } else if (strcmp(cmd, "STATE") == 0 && arg != NULL && strcmp(arg, "SLEEP") == 0) {
    stateSleep();
  } else if (strcmp(cmd, "STATE") == 0 && arg != NULL && strcmp(arg, "IDLE") == 0) {
    stateIdle();
  } else if (strcmp(cmd, "COUNT") == 0 && arg != NULL) {
    countdown(atoi(arg));
  } else if (strcmp(cmd, "GAZE") == 0 && arg != NULL && strcmp(arg, "WARN") == 0) {
    gazeWarn();
  } else if (strcmp(cmd, "GAZE") == 0 && arg != NULL && strcmp(arg, "OK") == 0) {
    gazeOk();
  } else if (strcmp(cmd, "SHUTTER") == 0) {
    doShutter();
  } else if (strcmp(cmd, "PROCESSING") == 0) {
    processing();
  } else if (strcmp(cmd, "DONE") == 0) {
    done();
  } else if (strcmp(cmd, "ERROR") == 0) {
    error(arg != NULL ? arg : "");
  } else if (strcmp(cmd, "LCD") == 0 && arg != NULL) {
    customLcd(arg);
  } else if (strcmp(cmd, "TEST") == 0) {
    selfTest();
  } else {
    ok = false;
  }

  if (arg != NULL) {
    arg[-1] = ':';  // restore the original text for the reply
  }
  Serial.print(ok ? F("ACK:") : F("ERR:UNKNOWN:"));
  Serial.println(cmd);
}

void readSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      rxBuf[rxLen] = '\0';
      if (rxLen > 0) handleCommand(rxBuf);
      rxLen = 0;
    } else if (rxLen < sizeof(rxBuf) - 1) {
      rxBuf[rxLen++] = c;
    }
  }
}

void readButton() {
  int reading = digitalRead(PIN_BUTTON);
  unsigned long now = millis();
  if (reading != lastReading) {
    lastChange = now;
    lastReading = reading;
  }
  if (now - lastChange > 30 && reading != buttonState) {
    buttonState = reading;
    if (buttonState == LOW && now - lastPress > 1000) {
      lastPress = now;
      Serial.println(F("BTN:START"));
      tone(PIN_BUZZER, 1500, 50);
    }
  }
}

void updateBlink() {
  if (!gazeBlink) return;
  unsigned long now = millis();
  if (now - lastBlink >= 250) {
    lastBlink = now;
    blinkOn = !blinkOn;
    digitalWrite(PIN_LED_RED, blinkOn ? HIGH : LOW);
  }
}

// ---------- Main ----------
void setup() {
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_LED_YELLOW, OUTPUT);
  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_FLASH, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);

  Serial.begin(BAUD);

  lcd.init();
  lcd.backlight();

  matrix.shutdown(0, false);   // wake up MAX7219
  matrix.setIntensity(0, 4);   // 0..15, keep low to save USB power
  matrix.clearDisplay(0);

  stateIdle();
  Serial.println(F("READY"));
}

void loop() {
  readSerial();
  readButton();
  updateBlink();
}
