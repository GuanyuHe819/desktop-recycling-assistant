import sys
import tempfile
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "computer"))
from app import App, ModelCompatibilityError, Stable, load_config
from dashboard import Dashboard, ModelLibrary


class DashboardTests(unittest.TestCase):
    def setUp(self):
        cfg = load_config("computer/config.example.json")
        cfg["bin_labels"] = ["paper", "plastic", "metal", "cardboard", "textile"]
        self.view = Dashboard(cfg["bin_labels"])
        self.app = App(cfg, False, self.view)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.app.events_file = Path(directory.name) / "events.csv"
        self.app.client.publish = Mock()
        self.app.connected = True
        self.app.last_board = time.monotonic()
        self.app.state, self.app.boot = "READY", "board-1"
        self.stable = Stable(2, .85)

    def action(self, name, value=None):
        reply = Future()
        self.view.actions.put((name, value, reply))
        selection = self.view.advance(self.app, self.stable)
        return (reply.result(), selection) if name == "model" else reply.result()

    def message(self, **fields):
        self.app.queue.put(("message", {"session": self.app.session, "boot": self.app.boot, **fields}))
        self.app.handle()

    def start(self):
        self.view.frame_at = time.monotonic()
        self.assertIsNone(self.action("start"))
        self.assertTrue(self.app.wait_empty)

    def arm(self, label="paper"):
        self.app.wait_empty = False
        self.app.arm(label, .96)
        txn = self.app.active["txn"]
        self.message(type="arm_ack", txn=txn, result="ARMED")
        return txn

    def result(self, txn, outcome, observed):
        self.message(type="event", txn=txn, event_id=f"result-{txn}", outcome=outcome,
                     expected=0, observed=observed)

    def ready(self):
        self.message(type="status", state="READY", bins=5)
        self.view.advance(self.app, self.stable)

    def test_start_requires_camera_and_ready_board(self):
        self.assertIsNotNone(self.action("start"))
        self.view.frame_at = time.monotonic()
        self.app.state = "WAIT_CLEAR"
        self.assertIsNotNone(self.action("start"))
        self.ready()
        self.stable.add("paper", .95)
        self.start()
        self.assertFalse(self.stable.values)

    def test_confirmed_count_modes_and_late_replay(self):
        self.start()
        for result, observed in [("MATCHED", 0), ("MISMATCH", 3), ("TIMEOUT", -1)]:
            txn = self.arm()
            self.result(txn, result, observed)
            self.result(txn, result, observed)
            self.ready()
            self.result(txn, result, observed)
        state = self.view.snapshot()
        counts = state["round"]
        self.assertEqual((counts["total"], counts["matched"], counts["mismatch"]), (2, 1, 1))
        self.assertEqual([bin["observed"] for bin in state["bins"]], [1, 0, 0, 1, 0])
        self.assertEqual([bin["recommended"] for bin in state["bins"]], [2, 0, 0, 0, 0])
        self.assertEqual(counts["recent"][0]["outcome"], "TIMEOUT")
        self.assertEqual(len(self.app.events_file.read_text(encoding="utf-8-sig").splitlines()), 4)

    def test_end_drains_inflight_and_new_round_retains_history(self):
        self.start()
        txn = self.arm()
        self.assertIsNone(self.action("end"))
        self.assertEqual(self.view.snapshot()["round"]["phase"], "ending")
        self.assertIsNotNone(self.action("start"))
        self.result(txn, "MATCHED", 0)
        self.view.advance(self.app, self.stable)
        self.assertEqual(self.view.snapshot()["round"]["phase"], "ended")
        self.assertEqual(self.view.snapshot()["round"]["total"], 1)
        history = self.app.events_file.read_bytes()
        self.ready()
        self.start()
        self.assertEqual(self.view.snapshot()["round"]["total"], 0)
        self.assertEqual(self.app.events_file.read_bytes(), history)

    def test_stale_board_renews_handshake_and_late_result_is_unknown(self):
        self.start()
        txn = self.arm()
        self.app.last_board = 0
        self.app.handle()
        self.assertEqual(self.app.state, "OFFLINE")
        self.assertIsNone(self.app.active)
        self.app.publish = Mock()
        self.app.heartbeat()
        self.assertEqual(self.app.publish.call_args.args[0]["type"], "hello")
        self.ready()
        self.result(txn, "MATCHED", 0)
        self.assertEqual(self.app.publish.call_args.args[0]["type"], "event_ack")
        self.assertEqual(self.view.snapshot()["round"]["total"], 0)

    def test_wrong_boot_or_invalid_result_does_not_count(self):
        self.start()
        txn = self.arm()
        self.message(type="event", boot="some-other-board", txn=txn, event_id="x",
                     outcome="MATCHED", expected=0, observed=0)
        self.assertFalse(self.view.snapshot()["round"]["recent"])
        self.result(txn, "MATCHED", 4)
        self.assertEqual(self.view.snapshot()["round"]["recent"][0]["outcome"], "UNVERIFIED")
        self.assertEqual(self.view.snapshot()["round"]["total"], 0)

    def test_sensor_fault_and_disconnect_do_not_count(self):
        self.start()
        txn = self.arm()
        self.result(txn, "SENSOR_FAULT", -1)
        self.message(type="status", state="FAULT", bins=5)
        self.view.update(self.app)
        self.assertEqual(self.view.snapshot()["board"], "FAULT")
        self.assertIsNone(self.app.active)
        self.ready()
        self.arm()
        self.app.queue.put(("disconnect", 1))
        self.app.handle()
        self.assertEqual(self.view.snapshot()["round"]["recent"][0]["outcome"], "CLIENT_CONNECTION_LOST_UNKNOWN")
        self.assertEqual(self.view.snapshot()["round"]["total"], 0)

    def test_model_library_discovers_complete_exports_and_preserves_session_model(self):
        folder = self.app.events_file.parent / "models"
        baseline = folder / "2026-11-18-dataset-baseline"
        candidate = folder / "2026-11-19-my-samples"
        for directory in (baseline, candidate):
            directory.mkdir(parents=True)
            (directory / "keras_model.h5").touch()
            (directory / "labels.txt").touch()
        (folder / "keras_model.h5").touch()
        library = ModelLibrary(folder, baseline / "keras_model.h5", baseline / "labels.txt")
        self.view.library = library
        self.view.model_started()
        choices = library.scan()
        key = f"folder:{candidate.name}"
        self.assertIn(key, choices)
        self.assertNotIn("root", choices)
        (folder / "labels.txt").touch()
        self.assertIn("root", library.scan(refresh=True))

        self.start()
        self.assertIsNotNone(self.action("model", key)[0])
        self.assertIsNone(self.action("end"))
        self.assertIsNotNone(self.action("model", "../invalid")[0])
        self.view.frame_at = time.monotonic()
        error, requested = self.action("model", key)
        self.assertIsNone(error)
        self.assertEqual(requested.key, key)
        self.assertFalse(self.view.can_start(self.app))
        self.view.finish_model(ModelCompatibilityError("Class names do not match."))
        self.assertEqual(self.view.snapshot()["models"]["active"], library.configured.key)
        self.assertEqual(self.view.snapshot()["models"]["error"], "Class names do not match.")
        self.assertIsNone(self.action("model", key)[0])
        self.view.finish_model()
        state = self.view.snapshot()
        self.assertEqual(state["models"]["active"], key)
        self.assertEqual(state["round"]["model"], "Dataset baseline · 2026-11-18")
        self.assertFalse(self.view.can_start(self.app))


if __name__ == "__main__":
    unittest.main()
