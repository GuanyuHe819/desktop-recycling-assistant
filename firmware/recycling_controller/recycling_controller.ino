// Libraries: WiFiNINA, PubSubClient, ArduinoJson 6, Adafruit VL53L0X,
// Adafruit SSD1306 and Adafruit GFX. Hardware configuration is unverified.
#include <Arduino.h>
#include <Wire.h>
#include <WiFiNINA.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Adafruit_VL53L0X.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "config.h"
#if __has_include("secrets.h")
#include "secrets.h"
#else
#error Copy secrets.example.h to secrets.h and configure Wi-Fi and broker first.
#endif
WiFiClient wifi;
PubSubClient mqtt(wifi);
Adafruit_VL53L0X sensors[5];
Adafruit_SSD1306 screen(128,64,&Wire,-1);
String commandTopic=String(TOPIC_ROOT)+"/command", eventTopic=String(TOPIC_ROOT)+"/event";
String bootId, sessionId, txnId, lastTxn, resultMessage, lastAck, eventId;
enum State {STARTING, READY, WAIT_ENTRY, FEEDBACK, WAIT_CLEAR, FAULT};
State state=STARTING;
bool covered[5]={}, valid[5]={};
unsigned outCount[5]={}, invalidCount[5]={};
int expected=-1, sensorIndex=0;
unsigned long startMs=0, feedbackMs=0, peerMs=0, heartbeatMs=0, retryMs=0, nextNetworkMs=0;
bool pendingResult=false, configOk=false, networkOk=false, screenOk=false;
unsigned resultRetries=0;
// 0 = no result pattern, 1 = all green, 2 = all red and buzzer.
int feedbackPattern=0, feedbackPhase=-1;
// Preserve diagnostic details so they can be requested after the serial monitor opens.
int faultSensor=-1, faultApi=0, faultRange=-1;
const char* stateName(){switch(state){case STARTING:return "STARTING";case READY:return "READY";case WAIT_ENTRY:return "WAIT_ENTRY";case FEEDBACK:return "FEEDBACK";case WAIT_CLEAR:return "WAIT_CLEAR";default:return "FAULT";}}
void outputPin(int pin,bool on){digitalWrite(pin,(on != LED_ACTIVE_LOW)?HIGH:LOW);}
void lightsOff(){for(int i=0;i<BIN_COUNT;i++){outputPin(RED_PINS[i],false);outputPin(GREEN_PINS[i],false);}}
void indicate(int bin){lightsOff();for(int i=0;i<BIN_COUNT;i++)outputPin(i==bin?GREEN_PINS[i]:RED_PINS[i],true);}
void showAllLamps(bool red, bool on){
 lightsOff();
 if(on)for(int i=0;i<BIN_COUNT;i++)outputPin(red?RED_PINS[i]:GREEN_PINS[i],true);
}
bool buzzerToneRunning=false;
void buzz(bool on){
 if(!BUZZER_ENABLED)return;
 if(on){
   if(!buzzerToneRunning){tone(BUZZER_PIN,BUZZER_FREQUENCY_HZ);buzzerToneRunning=true;}
 }else{
   if(buzzerToneRunning){noTone(BUZZER_PIN);buzzerToneRunning=false;pinMode(BUZZER_PIN,OUTPUT);}
   // Restore the tested active-low module's inactive level after tone().
   digitalWrite(BUZZER_PIN,BUZZER_ACTIVE_LOW?HIGH:LOW);
 }
}
bool allClear(){for(int i=0;i<BIN_COUNT;i++)if(!valid[i] || covered[i] || outCount[i]<CLEAR_SAMPLES)return false;return true;}
void emit(JsonDocument& doc){char buf[512];size_t n=serializeJson(doc,buf,sizeof(buf));if(n && n<sizeof(buf))mqtt.publish(eventTopic.c_str(),buf,false);}
void show(const char* a,const char* b=""){if(!screenOk)return;screen.clearDisplay();screen.setCursor(0,0);screen.println(a);screen.println(b);screen.display();}
void statusMsg(){StaticJsonDocument<256> d;d["type"]="status";d["boot"]=bootId;d["state"]=stateName();d["bins"]=BIN_COUNT;d["session"]=sessionId;emit(d);}
void ack(const char* answer){StaticJsonDocument<256>d;d["type"]="arm_ack";d["boot"]=bootId;d["session"]=sessionId;d["txn"]=txnId;d["result"]=answer;char buf[256];serializeJson(d,buf,sizeof(buf));lastAck=buf;mqtt.publish(eventTopic.c_str(),buf,false);}
void finishTxn(const char* outcome,int observed=-1){
 if(txnId.length()==0)return;
 lightsOff();buzz(false);
 StaticJsonDocument<384>d; d["type"]="event";d["boot"]=bootId;d["session"]=sessionId;d["txn"]=txnId;eventId=bootId+"-"+txnId;d["event_id"]=eventId;d["outcome"]=outcome;d["expected"]=expected;d["observed"]=observed;
 resultMessage="";serializeJson(d,resultMessage);pendingResult=true;resultRetries=0;retryMs=millis();lastTxn=txnId;state=FEEDBACK;feedbackMs=millis();
 feedbackPattern=strcmp(outcome,"MATCHED")==0?1:(strcmp(outcome,"MISMATCH")==0 && observed>=0?2:0);
 feedbackPhase=-1;
 show(outcome);mqtt.publish(eventTopic.c_str(),resultMessage.c_str(),false);
}
void callback(char*,byte* payload,unsigned len){
 StaticJsonDocument<512>d;if(deserializeJson(d,payload,len))return;
 String type=d["type"]|"";String boot=d["boot"]|"";String session=d["session"]|"";
 if(type=="hello" && session.length()>0){if(sessionId!=session && state==WAIT_ENTRY)finishTxn("DISCONNECTED");sessionId=session;peerMs=millis();statusMsg();return;}
 if(boot!=bootId || session!=sessionId || !session.length())return;
 if(type=="heartbeat"){peerMs=millis();return;}
 if(type=="event_ack"){String id=d["event_id"]|"";if(id==eventId)pendingResult=false;return;}
 if(type!="arm")return;
 String incoming=d["txn"]|"";int bin=d["bin"]|-1;String label=d["label"]|"";
 if(incoming.length()==0 || incoming.length()>64 || bin<0 || bin>=BIN_COUNT || label!=BIN_LABELS[bin])return;
 if(incoming==lastTxn || (incoming==txnId && txnId.length())){if(lastAck.length())mqtt.publish(eventTopic.c_str(),lastAck.c_str(),false);return;}
 if(state!=READY || pendingResult || !allClear()){StaticJsonDocument<256>r;r["type"]="arm_ack";r["boot"]=bootId;r["session"]=sessionId;r["txn"]=incoming;r["result"]="BUSY";emit(r);return;}
 txnId=incoming;expected=bin;startMs=millis();state=WAIT_ENTRY;indicate(bin);show("Please deposit",BIN_LABELS[bin]);ack("ARMED");
}
void printFaultDetails(){
 if(faultSensor<0){Serial.println(F("No recorded sensor measurement fault since boot."));return;}
 Serial.print(F("SENSOR FAULT sensor="));Serial.print(faultSensor+1);
 Serial.print(F(" api="));Serial.print(faultApi);
 Serial.print(F(" range_status="));Serial.println(faultRange);
 Serial.println(F("Range statuses are not necessarily hardware failures."));
}
void failSensors(int sensor, int api, int range){
 faultSensor=sensor;faultApi=api;faultRange=range;
 lightsOff();buzz(false);if(state==WAIT_ENTRY)finishTxn("SENSOR_FAULT");
 state=FAULT;
 char detail[24];snprintf(detail,sizeof(detail),"S%d API%d ST%d",sensor+1,api,range);
 show("Sensor fault",detail);printFaultDetails();
}
void scanOne(){
 int i=sensorIndex;sensorIndex=(sensorIndex+1)%BIN_COUNT;
 VL53L0X_RangingMeasurementData_t m = {};
 VL53L0X_Error error = sensors[i].rangingTest(&m,false);
 // For this open-air demo only, statuses 2 (weak signal) and 4 (phase fail)
 // count towards re-arming. Neither is a valid distance or proof of no object.
 // A trigger still requires an actual valid close measurement.
 if(error==VL53L0X_ERROR_NONE && (m.RangeStatus==2 || m.RangeStatus==4)){
   invalidCount[i]=0;valid[i]=true;
   outCount[i]=min(outCount[i]+1,(unsigned)CLEAR_SAMPLES);
   if(outCount[i]>=CLEAR_SAMPLES)covered[i]=false;
   return;
 }
 if(error!=VL53L0X_ERROR_NONE || m.RangeStatus!=0){valid[i]=false;outCount[i]=0;if(++invalidCount[i]>=3)failSensors(i,(int)error,(int)m.RangeStatus);return;}
 invalidCount[i]=0;valid[i]=true;uint16_t mm=m.RangeMilliMeter;
 if(!covered[i]){
   outCount[i]=(mm>=CLEAR_MM[i])?min(outCount[i]+1,(unsigned)CLEAR_SAMPLES):0;
   if(mm<=ENTER_MM[i]){
     covered[i]=true;outCount[i]=0;
     if(state==WAIT_ENTRY)finishTxn(i==expected?"MATCHED":"MISMATCH",i);
     else {Serial.print("UNARMED_ENTRY ");Serial.println(i+1);}
   }
 } else {
   outCount[i]=(mm>=CLEAR_MM[i])?min(outCount[i]+1,(unsigned)CLEAR_SAMPLES):0;
   if(outCount[i]>=CLEAR_SAMPLES)covered[i]=false;
 }
}
void setup(){
 Serial.begin(115200);
 // Stop before even changing GPIO modes until electrical and calibration review.
 if(!HARDWARE_VERIFIED){Serial.println("LOCKED: inspect electrical configuration and calibrate before enabling.");return;}
 for(int i=0;i<BIN_COUNT;i++){digitalWrite(RED_PINS[i],LED_ACTIVE_LOW?HIGH:LOW);pinMode(RED_PINS[i],OUTPUT);digitalWrite(GREEN_PINS[i],LED_ACTIVE_LOW?HIGH:LOW);pinMode(GREEN_PINS[i],OUTPUT);digitalWrite(XSHUT_PINS[i],LOW);pinMode(XSHUT_PINS[i],OUTPUT);}
 if(BUZZER_ENABLED){digitalWrite(BUZZER_PIN,BUZZER_ACTIVE_LOW?HIGH:LOW);pinMode(BUZZER_PIN,OUTPUT);}
 Wire.begin();delay(20);
 if(OLED_ENABLED){screenOk=screen.begin(SSD1306_SWITCHCAPVCC,OLED_ADDR);if(screenOk){screen.setTextColor(SSD1306_WHITE);screen.setTextSize(1);show("Starting...");}else{Serial.println("OLED init failed");state=FAULT;return;}}
 for(int i=0;i<BIN_COUNT;i++){pinMode(XSHUT_PINS[i],INPUT);delay(20);if(!sensors[i].begin(TOF_ADDRESS[i],false,&Wire)){Serial.print("ToF init failed ");Serial.println(i);state=FAULT;return;}}
 bootId=String(micros(),HEX)+String(analogRead(A7),HEX);mqtt.setServer(MQTT_HOST,MQTT_PORT);mqtt.setCallback(callback);mqtt.setBufferSize(512);configOk=true;state=WAIT_CLEAR;
}
void loop(){
 // '?' reports the last measurement fault even if the monitor opened late.
 while(Serial.available()){if(Serial.read()=='?')printFaultDetails();}
 if(!HARDWARE_VERIFIED || !configOk)return;
 unsigned long now=millis();
 if(WiFi.status()!=WL_CONNECTED){if(networkOk && state==WAIT_ENTRY)finishTxn("DISCONNECTED");networkOk=false;lightsOff();buzz(false);if(now-nextNetworkMs>=5000){nextNetworkMs=now;WiFi.begin(WIFI_SSID,WIFI_PASSWORD);}return;}
 if(!mqtt.connected()){if(networkOk && state==WAIT_ENTRY)finishTxn("DISCONNECTED");networkOk=false;lightsOff();buzz(false);if(now-nextNetworkMs>=5000){nextNetworkMs=now;String cid="recycling-"+bootId;if(mqtt.connect(cid.c_str())){mqtt.subscribe(commandTopic.c_str());sessionId="";state=WAIT_CLEAR;}}return;}
 networkOk=true;mqtt.loop();now=millis();
 if(sessionId.length() && now-peerMs>PEER_TIMEOUT_MS){if(state==WAIT_ENTRY)finishTxn("DISCONNECTED");sessionId="";lightsOff();buzz(false);if(state!=FAULT)state=WAIT_CLEAR;}
 if(state!=FAULT)scanOne();now=millis();
 if(state==WAIT_ENTRY && now-startMs>=LONG_MS)finishTxn("TIMEOUT");
 if(state==FEEDBACK){
   unsigned long elapsed=now-feedbackMs;
   if(elapsed>=FEEDBACK_MS){lightsOff();buzz(false);feedbackPattern=0;state=WAIT_CLEAR;txnId="";}
   else if(feedbackPattern){
     int phase=(int)(elapsed/FLASH_INTERVAL_MS);
     if(phase!=feedbackPhase){
       feedbackPhase=phase;
       bool on=(phase%2)==0;
       showAllLamps(feedbackPattern==2,on);
       buzz(feedbackPattern==2 && on);
     }
   }
 }
 if(state==WAIT_CLEAR && allClear() && sessionId.length() && !pendingResult){state=READY;show("Ready");}
 if(pendingResult && now-retryMs>=2000){retryMs=now;if(resultRetries++<5)mqtt.publish(eventTopic.c_str(),resultMessage.c_str(),false);else{pendingResult=false;state=FAULT;lightsOff();buzz(false);show("Event ack failed");Serial.println("Event acknowledgement failed; restart after investigation.");}}
 if(now-heartbeatMs>=2000){heartbeatMs=now;statusMsg();}
}
