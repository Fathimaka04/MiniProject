"""
Tests for connecting this app to the dashboard with a one-time code.
No dashboard, camera or network needed.

Run:
    python -m unittest test_pairing_link -v
"""
import json
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

import caregiver_link as cl
from test_caregiver_link import FakeAPI


class SavedLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patcher = mock.patch.object(cl, "LINK_FILE", Path(self.tmp.name) / "dashboard_link.json")
        self.patcher.start()
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("GAZEASSIST_API_URL", None)
        os.environ.pop("GAZEASSIST_API_TOKEN", None)

    def tearDown(self):
        self.env.stop()
        self.patcher.stop()
        self.tmp.cleanup()

    def test_token_is_encrypted_on_disk_and_reads_back(self):
        cl.save_link("http://127.0.0.1:8000/", "secret-token-123", "Ravi Menon", "WARD-PC")
        raw = cl.LINK_FILE.read_text(encoding="utf-8")
        self.assertNotIn("secret-token-123", raw)              # never stored readable
        saved = cl.load_saved_link()
        self.assertEqual(saved["token"], "secret-token-123")
        self.assertEqual(saved["url"], "http://127.0.0.1:8000")
        self.assertEqual(saved["patient_name"], "Ravi Menon")

    def test_corrupted_file_is_ignored_not_fatal(self):
        cl.LINK_FILE.write_text(json.dumps({"url": "x", "token_protected": "bm90LWRwYXBp"}), encoding="utf-8")
        with self.assertLogs("gazeassist.caregiver_link", level="WARNING"):
            self.assertIsNone(cl.load_saved_link())

    def test_no_pairing_and_no_env_means_not_configured(self):
        self.assertFalse(cl.CaregiverAPI().configured)

    def test_saved_pairing_beats_old_env_token(self):
        os.environ["GAZEASSIST_API_TOKEN"] = "old-env-token"
        cl.save_link("http://10.0.0.5:8000", "paired-token", "Ravi", "PC")
        api = cl.CaregiverAPI()
        self.assertEqual(api.token, "paired-token")
        self.assertEqual(api.base_url, "http://10.0.0.5:8000")

    def test_env_url_beats_saved_url(self):
        """run_all.py points the app at its own local dashboard."""
        cl.save_link("http://10.0.0.5:8000", "paired-token", "Ravi", "PC")
        os.environ["GAZEASSIST_API_URL"] = "http://127.0.0.1:8000"
        self.assertEqual(cl.CaregiverAPI().base_url, "http://127.0.0.1:8000")

    def test_env_token_still_works_without_pairing(self):
        os.environ["GAZEASSIST_API_TOKEN"] = "manual-token"
        self.assertEqual(cl.CaregiverAPI().token, "manual-token")

    def test_pair_device_error_messages(self):
        response = mock.Mock(status_code=400)
        response.json.return_value = {"detail": "This code is wrong, already used or expired."}
        with mock.patch.object(cl.requests, "post", return_value=response):
            with self.assertRaisesRegex(cl.CaregiverAPIError, "wrong, already used"):
                cl.pair_device("127.0.0.1:8000", "123456")
        with mock.patch.object(cl.requests, "post", side_effect=cl.requests.ConnectionError()):
            with self.assertRaisesRegex(cl.CaregiverAPIError, "Cannot reach the dashboard"):
                cl.pair_device("http://127.0.0.1:9", "123456")


class PairingWindowAndRelinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patcher = mock.patch.object(cl, "LINK_FILE", Path(self.tmp.name) / "dashboard_link.json")
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def pump(self, condition, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if condition():
                return
            time.sleep(0.01)
        self.fail("timed out")

    def test_window_pairs_saves_and_hands_over_a_ready_api(self):
        paired = []
        fake_reply = {"token": "tok-xyz", "patient": {"id": 7, "name": "Ravi Menon", "language": "ml"},
                      "base_url": "http://127.0.0.1:8000"}
        with mock.patch.object(cl, "pair_device", return_value=fake_reply) as pair:
            window = cl.PairingWindow(self.root, on_paired=paired.append)
            window.code_var.set("482 913")
            window._connect()
            self.pump(lambda: paired, timeout=5)
        pair.assert_called_once()
        self.assertEqual(pair.call_args[0][1], "482913")        # spaces removed
        self.assertEqual(paired[0].token, "tok-xyz")
        self.assertEqual(cl.load_saved_link()["token"], "tok-xyz")

    def test_window_rejects_short_code_without_network(self):
        with mock.patch.object(cl, "pair_device") as pair:
            window = cl.PairingWindow(self.root, on_paired=lambda api: None)
            window.code_var.set("12")
            window._connect()
            self.root.update()
            pair.assert_not_called()
            self.assertIn("6-digit", window.status.cget("text"))
            window._skip()

    def test_window_shows_server_error(self):
        with mock.patch.object(cl, "pair_device", side_effect=cl.CaregiverAPIError("Code expired")):
            window = cl.PairingWindow(self.root, on_paired=lambda api: None)
            window.code_var.set("111111")
            window._connect()
            self.pump(lambda: "Code expired" in window.status.cget("text"))
            window._skip()

    def test_link_switches_to_new_api_after_pairing(self):
        unconfigured = cl.CaregiverAPI("http://127.0.0.1:9", token="")
        statuses = []
        link = cl.CaregiverLink(self.root, api=unconfigured, on_status=lambda c, t: statuses.append((c, t)))
        link.DRAIN_MS = 20
        link.HEARTBEAT_S = 0.2
        link.start()
        self.pump(lambda: statuses)
        self.assertIn("Ctrl+Shift+P", statuses[-1][1])

        fake = FakeAPI()
        link.use_api(fake)
        self.pump(lambda: fake.heartbeats >= 2)
        self.pump(lambda: statuses[-1][0] is True)
        link.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
