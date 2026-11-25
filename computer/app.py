"""Computer client: Teachable Machine Keras camera inference + MQTT.
Run from project root: python computer/app.py --config computer/config.json
Manual debugging: add --manual (no AI model or camera needed).
"""
import argparse
import csv
import json
import queue
import re
import sys
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load_config(path):
    cfg = json.loads((ROOT / path).read_text(encoding="utf-8-sig"))
    if not 1 <= len(cfg["bin_labels"]) <= 5 or len(set(cfg["bin_labels"])) != len(cfg["bin_labels"]):
        raise ValueError("bin_labels must contain 1 to 5 distinct labels")
    if cfg["stable_frames"] < 1 or not 0 < cfg["confidence_threshold"] <= 1:
        raise ValueError("invalid stability settings")
    return cfg

@contextmanager
def controller_lock():
    """Only one project process may use the camera or control the board."""
    import msvcrt
    path = ROOT / "logs" / "controller.lock"
    path.parent.mkdir(exist_ok=True)
    with path.open("ab") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("Close the other Recycling Assistant or Dashboard first.") from error
        try:
            yield
        finally:
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)

class ModelCompatibilityError(ValueError):
    pass


def read_labels(path):
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    labels = [re.sub(r"^\s*\d+\s+", "", line).strip() for line in lines if line.strip()]
    if len(set(labels)) != len(labels):
        raise ModelCompatibilityError("labels.txt contains duplicate labels.")
    return labels

def check_labels(labels, expected):
    if set(labels) != set(expected) | {"empty"}:
        raise ModelCompatibilityError(f"Use exactly these class names in Teachable Machine: {', '.join([*expected, 'empty'])}.")


def load_classifier(model_path, labels_path, expected):
    import numpy as np
    from tensorflow.keras.models import load_model

    labels = read_labels(labels_path)
    check_labels(labels, expected)
    model = load_model(str(model_path), compile=False)
    if tuple(model.input_shape[1:]) != (224, 224, 3) or model.output_shape[-1] != len(labels):
        raise ModelCompatibilityError("The model must take 224 × 224 RGB images and return one score per label.")
    scores = model(np.zeros((1, 224, 224, 3), dtype=np.float32), training=False)
    if np.shape(scores) != (1, len(labels)) or not np.isfinite(scores).all():
        raise ModelCompatibilityError("The model did not return usable class scores.")
    return model, labels

class Stable:
    def __init__(self, count, threshold):
        self.values = deque(maxlen=count)
        self.threshold = threshold
    def add(self, label, score):
        self.values.append(label if score >= self.threshold else None)
        return label if len(self.values) == self.values.maxlen and all(x == label for x in self.values) else None
    def reset(self):
        self.values.clear()

class CameraModel:
    def __init__(self, model_path, labels_path, camera, expected, *, prepared=None):
        import cv2
        import numpy as np
        from PIL import Image, ImageOps
        self.cv2, self.np, self.Image, self.ImageOps = cv2, np, Image, ImageOps
        self.use_classifier(prepared if prepared is not None else load_classifier(model_path, labels_path, expected))
        self.cap = cv2.VideoCapture(camera)
        if not self.cap.isOpened():
            self.cap.release()
            raise RuntimeError(f"camera {camera} could not be opened")
    def use_classifier(self, classifier):
        self.model, self.labels = classifier
    def read(self):
        ok, frame = self.cap.read()
        if not ok: raise RuntimeError("camera read failed")
        rgb = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2RGB)
        im = self.Image.fromarray(rgb)
        im = self.ImageOps.fit(im, (224,224), method=self.Image.Resampling.LANCZOS)
        arr = (self.np.asarray(im).astype(self.np.float32) / 127.5 - 1.0)[None, ...]
        probs = self.model.predict(arr, verbose=0)[0]
        if not self.np.isfinite(probs).all(): raise ValueError("non-finite model output")
        idx = int(self.np.argmax(probs))
        return frame, self.labels[idx], float(probs[idx])
    def close(self): self.cap.release(); self.cv2.destroyAllWindows()

class App:
    def __init__(self, cfg, manual, view=None):
        import paho.mqtt.client as mqtt
        self.cfg, self.manual = cfg, manual
        self.view = view
        self.queue = queue.Queue()
        self.session = uuid.uuid4().hex
        self.boot = None
        self.state = "OFFLINE"
        self.last_board = 0
        self.active = None
        self.wait_empty = True
        self.seen = set()
        self.retired = set()
        self.notice = None
        self.events_file = ROOT / "logs" / "events.csv"
        self.events_file.parent.mkdir(exist_ok=True)
        self.client = mqtt.Client(client_id="recycling-pc-" + self.session[:12], clean_session=True)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.connected = False
        self.topic = cfg["topic_root"].rstrip("/")
    def publish(self, data):
        if self.connected:
            self.client.publish(self.topic+"/command", json.dumps(data), qos=0, retain=False)
    def on_connect(self, client, userdata, flags, rc):
        self.queue.put(("connect", rc))
        if rc == 0: client.subscribe(self.topic+"/event", qos=0)
    def on_disconnect(self, client, userdata, rc): self.queue.put(("disconnect", rc))
    def on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload)
            if isinstance(data, dict):
                self.queue.put(("message", data))
        except (ValueError,UnicodeDecodeError): pass
    def heartbeat(self):
        if self.boot is None or self.state == "OFFLINE":
            self.publish({"type": "hello", "session": self.session})
        else:
            self.publish({"type": "heartbeat", "session": self.session, "boot": self.boot})
    def log(self, data):
        fields = ["timestamp", "boot", "session", "txn", "event_id", "outcome", "expected", "observed", "label", "score"]
        row = {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), **data, **(self.active or {})}
        exists = self.events_file.exists() and self.events_file.stat().st_size > 0
        with self.events_file.open("a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            if not exists: writer.writeheader()
            writer.writerow(row)
            f.flush()
        if self.view:
            self.view.record({key: row.get(key) for key in fields})

    def abandon(self, outcome):
        if self.active and not self.active.get("finished"):
            self.log({"outcome": outcome, "session": self.session, "boot": self.boot})
            self.retired.add((self.boot, self.active["txn"]))
            self.notice = outcome
        self.active = None
        self.wait_empty = True

    def handle(self):
        while True:
            try: kind, data = self.queue.get_nowait()
            except queue.Empty: break
            if kind == "connect":
                if self.active:
                    self.abandon("CLIENT_CONNECTION_LOST_UNKNOWN")
                self.connected = data == 0
                self.boot = None; self.state = "OFFLINE"; self.wait_empty = True
                self.heartbeat()
            elif kind == "disconnect":
                if self.active:
                    print("Connection lost; transaction outcome may be unknown.")
                    self.abandon("CLIENT_CONNECTION_LOST_UNKNOWN")
                self.connected = False; self.boot = None; self.state = "OFFLINE"; self.wait_empty = True
            else:
                t = data.get("type")
                if t == "status":
                    if data.get("session") != self.session: continue
                    newboot = data.get("boot")
                    if not isinstance(newboot, str) or not newboot: continue
                    was_unavailable = self.state in {"OFFLINE", "FAULT", "CONFIG_MISMATCH"}
                    if self.boot and newboot != self.boot:
                        print("Board restarted.")
                        self.abandon("BOARD_RESTARTED_UNKNOWN")
                    board_state = data.get("state")
                    self.boot, self.last_board = newboot, time.monotonic()
                    self.state = board_state if isinstance(board_state, str) else "OFFLINE"
                    if data.get("bins") != len(self.cfg["bin_labels"]):
                        self.state = "CONFIG_MISMATCH"
                    if was_unavailable and self.state == "READY":
                        self.notice = None
                elif self.boot and data.get("session") == self.session and data.get("boot") == self.boot:
                    if t == "arm_ack" and self.active and not self.active.get("finished") and data.get("txn") == self.active["txn"]:
                        print("Board response:",data.get("result"))
                        if data.get("result") == "ARMED":
                            self.active["armed"] = True; self.state = "WAIT_ENTRY"
                        else:
                            self.notice = "ARM_REJECTED"
                            self.active = None
                            self.wait_empty = True
                    if t == "event":
                        txn = data.get("txn")
                        event_id = data.get("event_id")
                        if not isinstance(txn, str) or not isinstance(event_id, str) or not event_id:
                            continue
                        if event_id in self.seen or (self.boot, txn) in self.retired:
                            self.publish({"type":"event_ack","session":self.session,"boot":self.boot,"event_id":event_id})
                            continue
                        if not self.active or self.active.get("finished") or txn != self.active["txn"]:
                            continue
                        self.log(data); self.seen.add(event_id); print("Result:",data.get("outcome"),"observed:",data.get("observed"))
                        self.publish({"type":"event_ack","session":self.session,"boot":self.boot,"event_id":event_id})
                        self.active["finished"] = True
                        self.wait_empty = True
                        if self.state not in {"FAULT", "CONFIG_MISMATCH"}:
                            self.state = "FEEDBACK"
        if self.boot and time.monotonic()-self.last_board > self.cfg["board_timeout_seconds"]:
            self.abandon("BOARD_CONNECTION_LOST_UNKNOWN")
            self.state = "OFFLINE"
        if self.active and self.state in {"FAULT", "CONFIG_MISMATCH"}:
            self.abandon("BOARD_FAULT_UNKNOWN")
        if self.active and self.active.get("finished") and self.state == "READY":
            self.active = None
    def arm(self, label, score):
        if not self.connected or not self.boot or self.state != "READY" or self.active or self.wait_empty: return
        txn = uuid.uuid4().hex
        self.notice = None
        self.active={"txn":txn,"label":label,"score":score,"sent":time.monotonic(),"attempts":1}
        self.publish({"type":"arm","session":self.session,"boot":self.boot,"txn":txn,"bin":self.cfg["bin_labels"].index(label),"label":label,"score":score})
        print("Recommendation sent:",label,"waiting for ARMED; do not deposit yet.")
    def run(self):
        if self.view:
            self.client.reconnect_delay_set(min_delay=1, max_delay=5)
            self.client.connect_async(self.cfg["broker"], int(self.cfg["port"]), 30)
        else:
            self.client.connect(self.cfg["broker"], int(self.cfg["port"]), 30)
        self.client.loop_start()
        model=None
        loader = ThreadPoolExecutor(max_workers=1) if self.view else None
        model_load = None
        stable=Stable(self.cfg["stable_frames"],self.cfg["confidence_threshold"])
        last_heartbeat=0
        try:
            if self.manual:
                import msvcrt
                print("MANUAL DEBUG MODE: e = cleared, 1-5 = recommend, q = quit")
            else:
                try:
                    model=CameraModel(ROOT/self.cfg["model_file"],ROOT/self.cfg["labels_file"],self.cfg["camera_index"],self.cfg["bin_labels"])
                except Exception as error:
                    if not self.view: raise
                    self.view.camera_failed(error)
                else:
                    if self.view:
                        self.view.model_started()
            while True:
                previous = self.active
                self.handle(); now=time.monotonic()
                if self.active and not self.active.get("armed") and not self.active.get("finished") and now-self.active["sent"]>=2:
                    if self.active["attempts"]>=3:
                        print("No ARMED acknowledgement; stop and check board; outcome unknown.")
                        self.abandon("ARM_ACK_UNKNOWN")
                    else:
                        a=self.active;self.publish({"type":"arm","session":self.session,"boot":self.boot,"txn":a["txn"],"bin":self.cfg["bin_labels"].index(a["label"]),"label":a["label"],"score":a["score"]});a["sent"]=now;a["attempts"]+=1
                if now-last_heartbeat>=2: self.heartbeat();last_heartbeat=now
                if self.view and previous and not self.active:
                    stable.reset()
                if self.view:
                    requested = self.view.advance(self, stable)
                    if requested:
                        model_load = loader.submit(load_classifier, requested.model, requested.labels, self.cfg["bin_labels"])
                    if model_load and model_load.done():
                        try:
                            prepared = model_load.result()
                            if model:
                                model.use_classifier(prepared)
                            else:
                                model = CameraModel(None, None, self.cfg["camera_index"], self.cfg["bin_labels"], prepared=prepared)
                            stable.reset()
                            self.wait_empty = True
                            self.view.finish_model()
                        except Exception as error:
                            self.view.finish_model(error)
                        finally:
                            model_load = None
                if self.manual:
                    if msvcrt.kbhit():
                        key=msvcrt.getwch().lower()
                        if key=='q': break
                        if key=='e': self.wait_empty=False;print('Ready for next manual test')
                        if key in '12345' and int(key)<=len(self.cfg['bin_labels']): self.arm(self.cfg['bin_labels'][int(key)-1],1.0)
                    time.sleep(.03)
                elif model:
                    try:
                        frame, label, score = model.read()
                    except (OSError, ValueError, RuntimeError) as error:
                        if not self.view: raise
                        model.close()
                        model = None
                        self.view.camera_failed(error)
                        continue
                    agreed=stable.add(label,score)
                    if agreed=='empty' and not self.active: self.wait_empty=False
                    elif agreed and agreed!='empty' and not self.wait_empty and (not self.view or self.view.running):
                        self.arm(agreed,score)
                    if self.view:
                        self.view.show_frame(model.cv2, frame, label, score, agreed)
                        self.view.update(self)
                        continue
                    readiness = ('board not ready' if self.state != 'READY' else
                                 'waiting empty' if self.wait_empty else 'ready to recognize')
                    text=f"{label} {score:.2f} | board {self.state} | {readiness}"
                    model.cv2.putText(frame,text,(10,30),model.cv2.FONT_HERSHEY_SIMPLEX,.55,(0,255,0),2)
                    model.cv2.imshow('Recycling Assistant - q to quit',frame)
                    if model.cv2.waitKey(1)&255 == ord('q'): break
                else:
                    self.view.update(self)
                    time.sleep(.05)
        finally:
            if loader:
                loader.shutdown(wait=False, cancel_futures=True)
            if model: model.close()
            self.client.loop_stop(); self.client.disconnect()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',default='computer/config.json');parser.add_argument('--manual',action='store_true');parser.add_argument('--camera-only',action='store_true',help='Preview model and camera without MQTT or hardware');args=parser.parse_args()
    try:
        cfg = load_config(args.config)
        with controller_lock():
            run_cli(cfg, args)
    except (OSError,ValueError,RuntimeError,ImportError,TypeError) as e:
        print('Cannot start:',e,file=sys.stderr);sys.exit(1)

def run_cli(cfg, args):
    if args.camera_only:
        model = CameraModel(ROOT / cfg["model_file"], ROOT / cfg["labels_file"], cfg["camera_index"], cfg["bin_labels"])
        try:
            while True:
                frame, label, score = model.read()
                model.cv2.putText(frame, f"{label} {score:.2f}", (10, 30), model.cv2.FONT_HERSHEY_SIMPLEX, .8, (0, 255, 0), 2)
                model.cv2.imshow("Camera only - q to quit", frame)
                if model.cv2.waitKey(1) & 255 == ord("q"):
                    break
        finally:
            model.close()
    else:
        App(cfg, args.manual).run()


if __name__=='__main__': main()
