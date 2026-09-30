"""
Tests for caregiver_link.py — no dashboard, camera or network needed.

A FakeAPI stands in for the web dashboard so we can simulate it going offline,
rejecting data, acknowledging an SOS, sending messages, or hanging.

Run:
    python -m unittest test_caregiver_link -v
"""
import threading
import time
import tkinter as tk
import unittest

from caregiver_link import CaregiverAPI, CaregiverAPIError, CaregiverLink


class FakeAPI:
    """In-memory stand-in for CaregiverAPI."""

    base_url = "http://127.0.0.1:9"   # nothing listens here
    token = "test-token"
    timeout = 0.5
    configured = True

    def __init__(self):
        self.online = True
        self.block = None          # threading.Event: calls hang until it is set
        self.sent = []             # (id, phrase, is_emergency, pain_level)
        self.acked = set()
        self.messages = []
        self.heartbeats = 0
        self._next_id = 1
        self._lock = threading.Lock()

    def _check(self):
        if self.block is not None:
            self.block.wait()
        if not self.online:
            raise CaregiverAPIError("dashboard offline (fake)")

    def send_heartbeat(self):
        self._check()
        self.heartbeats += 1
        return {"ok": True, "patient": {"id": 1, "name": "Test Patient"},
                "pending_messages": len(self.messages)}

    def send_request(self, phrase, is_emergency=False, pain_level=None):
        self._check()
        if phrase == "invalid":
            raise CaregiverAPIError("dashboard error 400", 400)
        with self._lock:
            rid = self._next_id
            self._next_id += 1
        self.sent.append((rid, phrase, is_emergency, pain_level))
        return {"id": rid, "phrase": phrase, "is_emergency": is_emergency,
                "pain_level": pain_level, "acknowledged": False, "message": "Waiting for a caregiver"}

    def check_ack(self, request_id):
        self._check()
        emergency = any(r[0] == request_id and r[2] for r in self.sent)
        acked = request_id in self.acked
        return {"id": request_id, "is_emergency": emergency, "acknowledged": acked,
                "acknowledged_by": "Anna" if acked else None,
                "message": "Help is on the way" if acked else "Waiting for a caregiver"}

    def get_messages(self):
        self._check()
        messages, self.messages = self.messages, []
        return messages

    def close(self):
        pass


class CaregiverLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.api = FakeAPI()
        self.statuses, self.acks, self.messages, self.sent = [], [], [], []
        self.callback_threads = set()

        def record(bucket):
            def callback(*args):
                self.callback_threads.add(threading.current_thread().name)
                bucket.append(args if len(args) > 1 else args[0])
            return callback

        self.link = CaregiverLink(
            self.root, api=self.api,
            on_status=record(self.statuses), on_ack=record(self.acks),
            on_message=record(self.messages), on_sent=record(self.sent),
        )
        # Speed everything up for tests.
        self.link.HEARTBEAT_S = 0.2
        self.link.SOS_POLL_S = 0.1
        self.link.NORMAL_POLL_S = 0.1
        self.link.DRAIN_MS = 20

    def tearDown(self):
        if self.api.block is not None:
            self.api.block.set()
        self.link.stop()
        self.root.update()

    def pump(self, condition, timeout=5.0):
        """Run the Tk event loop until condition() is true."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if condition():
                return True
            time.sleep(0.01)
        self.fail("timed out waiting for condition")

    # ------------------------------------------------------------------

    def test_heartbeats_and_connected_status(self):
        self.link.start()
        self.pump(lambda: self.api.heartbeats >= 3)
        self.assertEqual(self.statuses[0][0], True)
        self.assertIn("Test Patient", self.statuses[0][1])

    def test_disabled_without_token(self):
        api = CaregiverAPI("http://127.0.0.1:9", token="")
        link = CaregiverLink(self.root, api=api, on_status=lambda c, t: self.statuses.append((c, t)))
        link.DRAIN_MS = 20
        link.start()
        link.submit_request("I am thirsty")
        self.pump(lambda: self.statuses)
        self.assertEqual(self.statuses[0], (False, "Caregiver board: not set up"))
        self.assertIsNone(link._thread)
        link.stop()

    def test_offline_requests_are_kept_and_sos_goes_first(self):
        self.api.online = False
        self.link.start()
        self.link.submit_request("Water")
        self.link.submit_request("Blanket")
        self.link.submit_request("SOS", is_emergency=True)
        self.pump(lambda: self.statuses and self.statuses[-1][0] is False)
        self.assertEqual(self.api.sent, [])

        self.api.online = True
        self.link._wake.set()
        self.pump(lambda: len(self.api.sent) == 3, timeout=10)
        self.assertEqual([s[1] for s in self.api.sent], ["SOS", "Water", "Blanket"])
        # The badge turns green again on the next heartbeat.
        self.pump(lambda: self.statuses[-1][0] is True)

    def test_pain_level_is_sent(self):
        self.link.start()
        self.link.submit_request("Pain level 7", pain_level=7)
        self.pump(lambda: self.api.sent)
        self.assertEqual(self.api.sent[0][3], 7)

    def test_sos_acknowledgement_reaches_the_tk_thread(self):
        self.link.start()
        self.link.submit_request("SOS", is_emergency=True)
        self.pump(lambda: self.sent)
        self.api.acked.add(self.sent[0]["id"])
        self.pump(lambda: self.acks)
        self.assertEqual(self.acks[0]["message"], "Help is on the way")
        self.assertEqual(self.callback_threads, {threading.main_thread().name})

    def test_caregiver_messages_are_delivered_once(self):
        self.api.messages = [{"id": 1, "text": "On my way", "sender": "Anna", "created_at": "now"}]
        self.link.start()
        self.pump(lambda: self.messages)
        self.assertEqual(self.messages[0]["text"], "On my way")
        time.sleep(0.5)
        self.root.update()
        self.assertEqual(len(self.messages), 1)

    def test_invalid_request_is_dropped_not_retried_forever(self):
        self.link.start()
        self.link.submit_request("invalid")
        self.link.submit_request("Water")
        self.pump(lambda: self.api.sent)
        self.assertEqual([s[1] for s in self.api.sent], ["Water"])

    def test_full_outbox_never_drops_an_sos(self):
        self.api.online = False
        self.link.MAX_OUTBOX = 3
        self.link.start()
        self.link.submit_request("SOS", is_emergency=True)
        for i in range(5):
            self.link.submit_request(f"Request {i}")
        with self.link._outbox_lock:
            queued = [item.phrase for item in self.link._outbox]
        self.assertEqual(queued, ["SOS", "Request 3", "Request 4"])

    def test_submit_never_blocks_the_ui(self):
        self.api.block = threading.Event()   # every API call hangs
        self.link.start()
        started = time.monotonic()
        for _ in range(50):
            self.link.submit_request("Water")
        self.root.update()
        self.assertLess(time.monotonic() - started, 0.2)

    def test_watchdog_restarts_a_stuck_thread(self):
        self.api.block = threading.Event()   # the first thread hangs forever
        self.link.STALL_S = 0.5
        self.link.start()
        self.pump(lambda: any("not responding" in text for _, text in self.statuses), timeout=5)
        self.assertEqual(self.link._generation, 2)
        self.assertIsInstance(self.link.api, CaregiverAPI)   # fresh HTTP session
        self.assertTrue(self.link._thread.is_alive())


if __name__ == "__main__":
    unittest.main(verbosity=2)
