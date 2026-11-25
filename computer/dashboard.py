"""Local browser view for the existing recycling controller."""
import argparse
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from collections import deque
from concurrent.futures import Future, TimeoutError
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app import App, ModelCompatibilityError, ROOT, controller_lock, load_config


HOST = "127.0.0.1"
ASSETS = Path(__file__).with_name("dashboard_web")
FRESH_SECONDS = 3
PREVIEW_INTERVAL = .03
CONFIRMED = {"MATCHED", "MISMATCH"}


def clock_text():
    return datetime.now().strftime("%H:%M:%S")


@dataclass(frozen=True)
class ModelChoice:
    key: str
    name: str
    model: Path
    labels: Path


class ModelLibrary:
    def __init__(self, folder, model_path, labels_path):
        self.folder = folder.resolve()
        model, labels = model_path.resolve(), labels_path.resolve()
        directory = model.parent
        if model.name == "keras_model.h5" and labels == directory / "labels.txt" and (
            directory == self.folder or directory.parent == self.folder
        ):
            self.configured = self.choice(directory)
        else:
            self.configured = ModelChoice("configured", "Configured model", model, labels)
        self.last_scan = float("-inf")
        self.choices = {}

    def choice(self, directory):
        folder_name = directory.name
        date = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:[-_ ]+(.+))?", folder_name)
        if date and date[2]:
            description = re.sub(r"[-_]+", " ", date[2])
            folder_name = f"{description[:1].upper() + description[1:]} · {date[1]}"
        key = f"folder:{directory.name}"
        if directory == self.folder:
            key, folder_name = "root", "Export in models folder"
        return ModelChoice(key, folder_name, directory / "keras_model.h5", directory / "labels.txt")

    def scan(self, refresh=False):
        now = time.monotonic()
        if not refresh and now - self.last_scan < 2:
            return self.choices
        choices = {self.configured.key: self.configured}
        try:
            directories = [self.folder, *sorted(self.folder.iterdir(), reverse=True)]
            for directory in directories:
                if directory != self.folder and directory.resolve().parent != self.folder:
                    continue
                choice = self.choice(directory)
                if all(path.is_file() and path.resolve().parent == directory for path in (choice.model, choice.labels)):
                    choices[choice.key] = choice
        except OSError:
            pass
        self.last_scan, self.choices = now, choices
        return choices


class Dashboard:
    def __init__(self, labels, launch_note=None, simulated=False, library=None, confidence_threshold=.85):
        self.labels = labels
        self.launch_note = launch_note
        self.simulated = simulated
        self.lock = threading.Lock()
        self.frame_ready = threading.Condition(self.lock)
        self.actions = queue.SimpleQueue()
        self.phase = "idle"
        self.started = self.ended = None
        self.recent = deque(maxlen=4)
        self.counts = dict.fromkeys(CONFIRMED, 0)
        self.recommended = [0] * len(labels)
        self.observed = [0] * len(labels)
        self.camera = {"phase": "loading"}
        self.jpeg = None
        self.frame_at = 0
        self.status = {}
        self.updated = 0
        self.broker_seen = False
        self.failure = None
        self.library = library
        self.active_model = self.requested_model = self.session_model = None
        self.model_error = None
        self.confidence_threshold = confidence_threshold

    @property
    def running(self):
        return self.phase == "running"

    def can_start(self, app):
        return (self.phase in {"idle", "ended"} and app.connected and app.boot
                and app.state == "READY" and not app.active and not self.requested_model
                and time.monotonic() - self.frame_at < FRESH_SECONDS)

    def can_choose_model(self, app):
        return (self.library is not None and self.phase in {"idle", "ended"}
                and not app.active and self.requested_model is None)

    def advance(self, app, stable):
        # App's loop owns state transitions so a click cannot race with arm or event handling.
        selection = None
        with self.lock:
            if self.phase == "ending" and (not app.active or app.active.get("finished")):
                self.phase, self.ended = "ended", clock_text()
            while True:
                try:
                    action, value, reply = self.actions.get_nowait()
                except queue.Empty:
                    return selection
                if not reply.set_running_or_notify_cancel():
                    continue
                error = None
                if action == "start" and self.can_start(app):
                    self.phase, self.started, self.ended = "running", clock_text(), None
                    self.session_model = self.active_model
                    self.model_error = None
                    self.recent.clear()
                    self.counts = dict.fromkeys(CONFIRMED, 0)
                    self.recommended = [0] * len(self.labels)
                    self.observed = [0] * len(self.labels)
                    app.wait_empty, app.notice = True, None
                    stable.reset()
                elif action == "end" and self.running:
                    if app.active and not app.active.get("finished"):
                        self.phase = "ending"
                    else:
                        self.phase, self.ended = "ended", clock_text()
                elif action == "model":
                    if not self.can_choose_model(app):
                        error = "Finish the session and wait for the station to clear before changing models."
                    else:
                        selection, error = self.select_model(value)
                else:
                    error = "This action is unavailable. Check the equipment and session status."
                reply.set_result(error)

    def select_model(self, key):
        choice = self.library.scan(refresh=True).get(key)
        if choice is None:
            return None, "This model was not found. Keep keras_model.h5 and labels.txt together in the models folder."
        self.requested_model, self.model_error = choice, None
        return choice, None

    def model_started(self):
        if self.library:
            with self.lock:
                self.active_model = self.library.configured

    def finish_model(self, error=None):
        with self.lock:
            if error is None:
                self.active_model = self.requested_model
                self.camera = {"phase": "loading"}
                self.jpeg, self.frame_at = None, 0
            else:
                print(f"Model switch failed: {error}", file=sys.stderr)
                message = "Could not load this Keras export. Check the launcher window."
                if isinstance(error, ModelCompatibilityError):
                    message = str(error)
                self.model_error = message
            self.requested_model = None

    def record(self, row):
        outcome = row["outcome"] if isinstance(row["outcome"], str) else "UNVERIFIED"
        expected, observed = row["expected"], row["observed"]
        confirmed = (outcome in CONFIRMED
                     and type(expected) is int and 0 <= expected < len(self.labels)
                     and type(observed) is int and 0 <= observed < len(self.labels)
                     and self.labels[expected] == row["label"]
                     and (expected == observed) == (outcome == "MATCHED"))
        with self.lock:
            if self.phase not in {"running", "ending"}:
                return
            if confirmed:
                self.counts[outcome] += 1
                self.recommended[expected] += 1
                self.observed[observed] += 1
            self.recent.appendleft({
                "time": row["timestamp"].split(" ")[-1],
                "label": row["label"],
                "actual": observed + 1 if confirmed else None,
                "outcome": outcome if confirmed or outcome not in CONFIRMED else "UNVERIFIED",
                "confirmed": confirmed,
            })

    def camera_failed(self, error):
        print(f"Camera unavailable: {error}", file=sys.stderr)
        with self.frame_ready:
            self.camera = {"phase": "error"}
            self.jpeg, self.frame_at = None, 0
            self.frame_ready.notify_all()

    def show_frame(self, cv2, frame, label, score, agreed):
        now = time.monotonic()
        jpeg = None
        if now - self.frame_at >= PREVIEW_INTERVAL:
            height, width = frame.shape[:2]
            if width > 896:
                frame = cv2.resize(frame, (896, round(height * 896 / width)), interpolation=cv2.INTER_AREA)
            ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
            if ok:
                jpeg = encoded.tobytes()
        with self.frame_ready:
            if jpeg:
                self.jpeg, self.frame_at = jpeg, now
                self.frame_ready.notify_all()
            self.camera = {"phase": "live", "label": label, "score": score, "stable": agreed == label,
                           "confident": score >= self.confidence_threshold}

    def update(self, app):
        active = app.active or {}
        with self.lock:
            self.broker_seen = self.broker_seen or app.connected
            self.status = {
                "broker": app.connected, "broker_lost": self.broker_seen and not app.connected,
                "board": app.state, "board_seen": bool(app.last_board),
                "recommendation": active.get("label"),
                "armed": (bool(active.get("armed")) and not active.get("finished")
                          and app.connected and app.state == "WAIT_ENTRY"),
                "pending": bool(active) and not active.get("finished"),
                "wait_empty": app.wait_empty, "can_start": bool(self.can_start(app)),
                "can_choose_model": self.can_choose_model(app),
                "notice": app.notice,
            }
            self.updated = time.monotonic()

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            camera = self.camera
            if camera["phase"] == "live" and now - self.frame_at >= FRESH_SECONDS:
                camera = {"phase": "stalled"}
            status = self.status
            if self.updated and now - self.updated >= FRESH_SECONDS:
                status = {"board": "UNKNOWN"}
            model_status = None
            if self.library:
                options = self.library.scan().copy()
                for choice in (self.active_model, self.requested_model):
                    if choice:
                        options.setdefault(choice.key, choice)
                model_status = {
                    "options": [{"id": choice.key, "name": choice.name} for choice in options.values()],
                    "active": self.active_model.key if self.active_model else None,
                    "pending": self.requested_model.key if self.requested_model else None,
                    "error": self.model_error,
                }
            return {
                **status, "camera": camera, "launch_note": self.launch_note, "failure": self.failure,
                "simulated": self.simulated, "models": model_status,
                "round": {"phase": self.phase, "started": self.started, "ended": self.ended,
                          "model": self.session_model.name if self.session_model else None,
                          "matched": self.counts["MATCHED"], "mismatch": self.counts["MISMATCH"],
                          "total": sum(self.counts.values()), "recent": list(self.recent)},
                "bins": [{"label": label, "observed": self.observed[i], "recommended": self.recommended[i]}
                         for i, label in enumerate(self.labels)],
            }

    def frame_after(self, previous):
        with self.frame_ready:
            self.frame_ready.wait_for(
                lambda: (self.jpeg is not None and self.frame_at != previous) or self.camera["phase"] == "error",
                timeout=FRESH_SECONDS,
            )
            if self.frame_at != previous and time.monotonic() - self.frame_at < FRESH_SECONDS:
                return self.frame_at, self.jpeg
            return 0, None


class LocalServer(ThreadingHTTPServer):
    # Don't let a browser closing a feed hold up Ctrl+C or server shutdown.
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, port, dashboard):
        self.dashboard = dashboard
        super().__init__((HOST, port), RequestHandler)
        self.authority = f"{HOST}:{self.server_port}"


class RequestHandler(BaseHTTPRequestHandler):
    static = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/style.css": ("style.css", "text/css; charset=utf-8"),
        "/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
    }

    def log_message(self, format, *args):
        pass

    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def send_headers(self, status, content_type, length=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def send_data(self, status, content_type, body):
        self.send_headers(status, content_type, len(body))
        self.wfile.write(body)

    def send_json(self, status, body):
        self.send_data(status, "application/json; charset=utf-8", json.dumps(body, ensure_ascii=False).encode())

    def do_GET(self):
        if self.headers_host_invalid():
            self.send_error(403)
            return
        if self.path in self.static:
            filename, content_type = self.static[self.path]
            self.send_data(200, content_type, (ASSETS / filename).read_bytes())
        elif self.path == "/api/status":
            self.send_json(200, self.server.dashboard.snapshot())
        elif self.path == "/camera.mjpg":
            self.stream_frames()
        elif self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self.send_error(404)

    def headers_host_invalid(self):
        return self.headers.get("Host") != self.server.authority

    def stream_frames(self):
        self.send_headers(200, "multipart/x-mixed-replace; boundary=frame")
        last = 0
        try:
            while True:
                timestamp, jpeg = self.server.dashboard.frame_after(last)
                if not jpeg:
                    return
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                last = timestamp
        except (BrokenPipeError, ConnectionResetError, TimeoutError, ConnectionAbortedError):
            pass

    def do_POST(self):
        origin = self.headers.get("Origin")
        if (self.headers_host_invalid() or self.headers.get("X-Recycling-Dashboard") != "1"
                or origin not in (None, f"http://{self.server.authority}")):
            self.send_error(403)
            return
        action = {"/api/round/start": "start", "/api/round/end": "end", "/api/models/select": "model"}.get(self.path)
        if not action:
            self.send_error(404)
            return
        value = None
        if action == "model":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2048:
                    raise ValueError()
                payload = json.loads(self.rfile.read(length))
                value = payload.get("id") if isinstance(payload, dict) else None
                if not isinstance(value, str) or not value:
                    raise ValueError()
            except (ValueError, TimeoutError):
                self.send_json(400, {"error": "Select one of the listed models."})
                return
        reply = Future()
        self.server.dashboard.actions.put((action, value, reply))
        try:
            error = reply.result(timeout=3)
        except TimeoutError:
            if reply.cancel():
                self.send_json(503, {"error": "The dashboard is not responding. Check the launcher window."})
                return
            error = reply.result()
        self.send_json(409 if error else (202 if action == "model" else 200), {"error": error})


@contextmanager
def project_broker(cfg, start):
    if not start:
        yield None
        return
    try:
        with socket.create_connection((cfg["broker"], int(cfg["port"])), timeout=.5):
            pass
    except OSError:
        pass
    else:
        print("Using the existing MQTT listener.", flush=True)
        yield None
        return
    exe = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Mosquitto" / "mosquitto.exe"
    process = None
    error = None
    try:
        try:
            with (ROOT / "logs" / "dashboard-broker.log").open("wb") as log:
                process = subprocess.Popen([str(exe), "-c", str(ROOT / "mosquitto-demo.conf")],
                                           stdout=log, stderr=subprocess.STDOUT)
            try:
                process.wait(timeout=.8)
                error = "Mosquitto could not start. Check logs/dashboard-broker.log."
            except subprocess.TimeoutExpired:
                print("Started the project Mosquitto broker.", flush=True)
        except OSError:
            error = "Could not launch Mosquitto. Check that it is installed."
        yield error
    finally:
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="computer/config.json")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="open the Dashboard in the default browser")
    parser.add_argument("--start-broker", action="store_true", help="run the prepared project Mosquitto listener")
    parser.add_argument("--network-unprepared", action="store_true", help="show a hotspot preparation notice")
    args = parser.parse_args()
    try:
        cfg = load_config(args.config)
        with controller_lock():
            library = ModelLibrary(ROOT / "models", ROOT / cfg["model_file"], ROOT / cfg["labels_file"])
            dashboard = Dashboard(cfg["bin_labels"], library=library, confidence_threshold=cfg["confidence_threshold"])
            with LocalServer(args.port, dashboard) as server, project_broker(cfg, args.start_broker) as broker_error:
                dashboard.launch_note = broker_error
                if args.network_unprepared:
                    dashboard.launch_note = "Hotspot setup did not finish. Join the classroom hotspot and restart, or check the launcher window."
                app = App(cfg, False, dashboard)
                url = f"http://{server.authority}/"
                threading.Thread(target=server.serve_forever, daemon=True).start()
                try:
                    print(f"Dashboard open at {url}  |  Ctrl+C to stop.", flush=True)
                    if args.open:
                        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
                    try:
                        app.run()
                    except Exception:
                        traceback.print_exc()
                        dashboard.camera_failed("Recognition stopped.")
                        with dashboard.lock:
                            dashboard.failure = "The controller stopped. Check the launcher window and restart the dashboard."
                            dashboard.status = {}
                        threading.Event().wait()
                finally:
                    server.shutdown()
    except KeyboardInterrupt:
        print("\nDashboard stopped.", flush=True)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        parser.exit(1, f"Cannot start Dashboard: {error}\n")


if __name__ == "__main__":
    main()
