"""
GazeAssist — link to the caregiver web dashboard (gazeassist_web, Django).

The gaze app never touches the dashboard's database. It talks to the
dashboard's REST API with the patient's token:

    POST /api/heartbeat/                 "I'm running"  -> patient shows Online
    POST /api/requests/                  a phrase, SOS or pain level
    GET  /api/requests/<id>/status/      has a caregiver acknowledged it?
    GET  /api/messages/pending/          messages a caregiver sent to the patient

Setup — no files to edit. A caregiver clicks "Connect gaze app" on the
dashboard, gets a 6-digit code, and types it into this app's "Connect to
caregiver dashboard" window (opens on first start, or press Ctrl+Shift+P).
The app swaps the code for its API token (POST /api/pair/) and saves it,
encrypted with Windows DPAPI, in data/dashboard_link.json.

Older manual setup still works: GAZEASSIST_API_URL / GAZEASSIST_API_TOKEN in
.env (a saved pairing takes priority over the .env token).

What is in this file
--------------------
1. CaregiverAPI — the four calls: send_heartbeat(), send_request(),
   check_ack(), get_messages(). They are ordinary blocking HTTP calls with a
   short timeout; never call them on the Tkinter thread.
2. Module-level send_heartbeat() / send_request() / check_ack() /
   get_messages() — the same four calls using settings from .env, handy for
   quick tests and scripts.
3. CaregiverLink — runs everything on ONE background thread so the UI never
   freezes:
     * heartbeat every 5 s (the dashboard shows Online / Offline)
     * phrases/SOS/pain go into an outbox and are sent in the background;
       if the dashboard is unreachable they are kept and re-sent later
       (an SOS jumps the queue)
     * watches each SOS until a caregiver acknowledges it -> on_ack()
     * fetches caregiver messages when the heartbeat says there are some
       -> on_message()
   Results are handed back to the Tkinter thread through a queue that is
   drained with root.after(), because Tk widgets must only be touched from
   the thread that created them.
4. NoticeOverlay — large on-screen notices for the patient ("Help is on the
   way", caregiver messages) and a small connection badge.

If GAZEASSIST_API_TOKEN is not set, CaregiverLink does nothing and the app
works exactly as before.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import queue
import socket
import threading
import time
import tkinter as tk
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import requests

logger = logging.getLogger("gazeassist.caregiver_link")

DEFAULT_API_URL = "http://127.0.0.1:8000"
USER_AGENT = "GazeAssist-TkApp/1.0"


# ══════════════════════════════════════════════════════════════════════
# 0. The saved pairing (dashboard address + encrypted token)
# ══════════════════════════════════════════════════════════════════════

LINK_FILE = Path(__file__).resolve().parent / "data" / "dashboard_link.json"


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Encrypt/decrypt with Windows DPAPI: only this Windows user on this
    computer can decrypt. Elsewhere the data is stored as-is (base64)."""
    if os.name != "nt":
        return data
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    func = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    func.argtypes = [ctypes.POINTER(BLOB), ctypes.c_wchar_p, ctypes.POINTER(BLOB), ctypes.c_void_p,
                     ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(BLOB)]
    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    args = (ctypes.byref(blob_in), "GazeAssist dashboard token" if protect else None, None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not func(*args):
        raise OSError(f"DPAPI {'protect' if protect else 'unprotect'} failed ({ctypes.get_last_error()})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def load_saved_link() -> Optional[dict]:
    """The saved pairing {url, token, patient_name, device_name, paired_at}, or None."""
    try:
        data = json.loads(LINK_FILE.read_text(encoding="utf-8"))
        data["token"] = _dpapi(base64.b64decode(data.pop("token_protected")), protect=False).decode()
        return data
    except FileNotFoundError:
        return None
    except Exception as exc:  # corrupted, or copied from another user/computer
        logger.warning("Caregiver link: saved pairing could not be read (%s) — connect again", exc)
        return None


def save_link(url: str, token: str, patient_name: str, device_name: str) -> None:
    LINK_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": url.rstrip("/"),
        "token_protected": base64.b64encode(_dpapi(token.encode(), protect=True)).decode(),
        "patient_name": patient_name,
        "device_name": device_name,
        "paired_at": datetime.now().isoformat(timespec="seconds"),
    }
    tmp = LINK_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(LINK_FILE)


def forget_link() -> None:
    try:
        LINK_FILE.unlink()
    except FileNotFoundError:
        pass


def default_dashboard_url() -> str:
    saved = load_saved_link()
    return (os.environ.get("GAZEASSIST_API_URL") or (saved or {}).get("url") or DEFAULT_API_URL).rstrip("/")


def pair_device(base_url: str, code: str, device_name: Optional[str] = None, timeout: float = 8.0) -> dict:
    """Swap a one-time code from the dashboard for this patient's API token.

    Returns {"token", "patient": {"id", "name", "language"}, ...}. Raises
    CaregiverAPIError with a message suitable for showing on screen.
    """
    base_url = base_url.strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        base_url = "http://" + base_url
    device_name = (device_name or socket.gethostname() or "Gaze app")[:100]
    try:
        resp = requests.post(f"{base_url}/api/pair/", json={"code": code, "device_name": device_name},
                             headers={"Accept": "application/json", "User-Agent": USER_AGENT}, timeout=timeout)
    except requests.RequestException as exc:
        raise CaregiverAPIError(f"Cannot reach the dashboard at {base_url}. Is it running? ({exc.__class__.__name__})") from exc
    try:
        data = resp.json()
    except ValueError:
        raise CaregiverAPIError(f"{base_url} does not look like the GazeAssist dashboard.", resp.status_code)
    if resp.status_code == 429:
        raise CaregiverAPIError("Too many attempts. Wait a minute and try again.", 429)
    if resp.status_code != 200 or "token" not in data:
        raise CaregiverAPIError(data.get("detail") or "The code was not accepted.", resp.status_code)
    data["base_url"] = base_url
    return data


# ══════════════════════════════════════════════════════════════════════
# 1. The four API calls (blocking)
# ══════════════════════════════════════════════════════════════════════

class CaregiverAPIError(Exception):
    """The dashboard could not be reached or returned an error."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class TokenRejectedError(CaregiverAPIError):
    """401/403: the token is wrong or revoked. Retrying quickly won't help."""


class CaregiverAPI:
    """Blocking client for the dashboard's gaze-app API.

    One instance per thread: ``requests.Session`` is not thread-safe.
    """

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None,
                 timeout: float = 5.0):
        saved = load_saved_link() if (base_url is None or token is None) else None
        # Address: explicit > GAZEASSIST_API_URL (.env / run_all.py) > saved pairing > default
        self.base_url = (base_url or os.environ.get("GAZEASSIST_API_URL")
                         or (saved or {}).get("url") or DEFAULT_API_URL).rstrip("/")
        # Token: explicit > saved pairing > GAZEASSIST_API_TOKEN in .env (older manual setup)
        if token is None:
            token = (saved or {}).get("token") or os.environ.get("GAZEASSIST_API_TOKEN", "")
        self.token = token.strip()
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Token {self.token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        })

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def close(self) -> None:
        self._session.close()

    # -- the four calls ---------------------------------------------------

    def send_heartbeat(self) -> dict:
        """Tell the dashboard the app is running.

        Returns e.g. {"ok": true, "patient": {"id": 7, "name": "Ravi Menon"},
        "server_time": "...", "pending_messages": 0, "online_window_seconds": 15}
        """
        return self._call("POST", "heartbeat/")

    def send_request(self, phrase: str, is_emergency: bool = False,
                     pain_level: Optional[int] = None) -> dict:
        """Record a phrase the patient selected. Returns the created request,
        including its ``id`` (use it with check_ack)."""
        body: dict[str, Any] = {"phrase": phrase.strip()[:255], "is_emergency": bool(is_emergency)}
        if pain_level is not None:
            body["pain_level"] = max(0, min(10, int(pain_level)))
        return self._call("POST", "requests/", json=body)

    def check_ack(self, request_id: int) -> dict:
        """Has a caregiver acknowledged this request?

        Returns e.g. {"id": 42, "acknowledged": true, "acknowledged_by": "Anna Joseph",
        "message": "Help is on the way", ...}
        """
        return self._call("GET", f"requests/{int(request_id)}/status/")

    def get_messages(self) -> list[dict]:
        """Caregiver messages not yet shown. Each is returned only once.

        Returns a list of {"id", "text", "sender", "created_at"}.
        """
        return self._call("GET", "messages/pending/").get("messages", [])

    # -- plumbing ---------------------------------------------------------

    def _call(self, method: str, path: str, json: Optional[dict] = None) -> dict:
        url = f"{self.base_url}/api/{path}"
        try:
            resp = self._session.request(method, url, json=json, timeout=self.timeout)
        except requests.RequestException as exc:
            raise CaregiverAPIError(
                f"cannot reach the caregiver dashboard at {self.base_url} ({exc.__class__.__name__})"
            ) from exc

        if resp.status_code in (401, 403):
            raise TokenRejectedError(
                "the dashboard rejected the API token — check GAZEASSIST_API_TOKEN in .env",
                resp.status_code,
            )
        if resp.status_code == 429:
            raise CaregiverAPIError("the dashboard asked us to slow down (429)", 429)
        if resp.status_code >= 400:
            raise CaregiverAPIError(f"dashboard error {resp.status_code}: {resp.text[:200]}", resp.status_code)
        try:
            return resp.json()
        except ValueError as exc:
            raise CaregiverAPIError(
                "the dashboard sent a non-JSON reply — is GAZEASSIST_API_URL correct?", resp.status_code
            ) from exc


# ══════════════════════════════════════════════════════════════════════
# 2. Module-level shortcuts (blocking — for scripts and quick tests)
# ══════════════════════════════════════════════════════════════════════

_default_api: Optional[CaregiverAPI] = None
_default_lock = threading.Lock()


def _api() -> CaregiverAPI:
    global _default_api
    with _default_lock:
        if _default_api is None:
            _default_api = CaregiverAPI()
        return _default_api


def send_heartbeat() -> dict:
    """POST /api/heartbeat/ using settings from .env. Blocking."""
    return _api().send_heartbeat()


def send_request(phrase: str, is_emergency: bool = False, pain_level: Optional[int] = None) -> dict:
    """POST /api/requests/ using settings from .env. Blocking."""
    return _api().send_request(phrase, is_emergency, pain_level)


def check_ack(request_id: int) -> dict:
    """GET /api/requests/<id>/status/ using settings from .env. Blocking."""
    return _api().check_ack(request_id)


def get_messages() -> list[dict]:
    """GET /api/messages/pending/ using settings from .env. Blocking."""
    return _api().get_messages()


# ══════════════════════════════════════════════════════════════════════
# 3. CaregiverLink — background thread + Tkinter-safe callbacks
# ══════════════════════════════════════════════════════════════════════

@dataclass
class _Outgoing:
    phrase: str
    is_emergency: bool
    pain_level: Optional[int]
    queued_at: float


@dataclass
class _Watch:
    is_emergency: bool
    started: float
    next_check: float


class CaregiverLink:
    """Keeps the gaze app connected to the caregiver dashboard.

    Usage (in the Tkinter app)::

        link = CaregiverLink(root, on_ack=..., on_message=..., on_status=...)
        link.start()
        link.submit_request("I am thirsty")                 # never blocks
        link.submit_request("SOS", is_emergency=True)
        link.submit_request("Pain level 6", pain_level=6)
        ...
        link.stop()                                          # on close

    Callbacks always run on the Tkinter thread, so they may update widgets:
        on_status(connected: bool, text: str)  connection went up / down
        on_ack(status: dict)                   a caregiver acknowledged a request
        on_message(message: dict)              a caregiver sent a message
        on_sent(request: dict)                 a request reached the dashboard
    """

    HEARTBEAT_S = 5.0            # dashboard shows Offline after 15 s without one
    SOS_POLL_S = 2.0             # how often to ask "has anyone acknowledged the SOS?"
    NORMAL_POLL_S = 6.0          # same for normal requests ("caregiver has seen it")
    SOS_WATCH_MAX_S = 60 * 60    # stop asking after an hour
    NORMAL_WATCH_MAX_S = 10 * 60
    MAX_NORMAL_WATCHES = 5       # only the latest few normal requests are watched
    MAX_OUTBOX = 200             # requests kept while offline
    MAX_BACKOFF_S = 30.0
    TOKEN_RETRY_S = 60.0
    STALL_S = 60.0               # watchdog: restart the thread if it makes no progress this long
    DRAIN_MS = 150               # how often the Tk thread picks up results

    def __init__(
        self,
        root: tk.Misc,
        api: Optional[CaregiverAPI] = None,
        on_status: Optional[Callable[[bool, str], None]] = None,
        on_ack: Optional[Callable[[dict], None]] = None,
        on_message: Optional[Callable[[dict], None]] = None,
        on_sent: Optional[Callable[[dict], None]] = None,
        watch_normal_requests: bool = True,
    ):
        self.root = root
        self.api = api or CaregiverAPI()
        self.on_status = on_status
        self.on_ack = on_ack
        self.on_message = on_message
        self.on_sent = on_sent
        self.watch_normal_requests = watch_normal_requests

        self._events: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self._outbox: deque[_Outgoing] = deque()
        self._outbox_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._generation = 0         # bumped on restart; an old thread exits when it sees a newer one
        self._last_progress = time.monotonic()
        self._drain_job: Optional[str] = None

        # Worker-thread-only state
        self._watches: dict[int, _Watch] = {}
        self._connected: Optional[bool] = None
        self._backoff = 0.0

    # -- lifecycle (Tk thread) --------------------------------------------

    @property
    def enabled(self) -> bool:
        return self.api.configured

    def start(self) -> None:
        """Start the background thread (does nothing if no token is configured)."""
        self._schedule_drain()
        if not self.enabled:
            logger.warning("Caregiver link off: not connected yet — press Ctrl+Shift+P and enter the dashboard's code")
            self._events.put(("status", (False, "Caregiver board: not connected — press Ctrl+Shift+P")))
            return
        if self._thread and self._thread.is_alive():
            return
        self._start_thread()
        logger.info("Caregiver link started → %s", self.api.base_url)

    def _start_thread(self) -> None:
        self._generation += 1
        self._last_progress = time.monotonic()
        self._thread = threading.Thread(
            target=self._run, args=(self._generation,),
            name=f"caregiver-link-{self._generation}", daemon=True,
        )
        self._thread.start()

    def use_api(self, api: CaregiverAPI) -> None:
        """Switch to a new dashboard/token (after pairing) without restarting the app.

        The old worker thread notices the newer generation and exits on its own.
        """
        self.api = api
        self._connected = None
        self._watches.clear()
        self._wake.set()
        if not self.enabled:
            return
        if self._drain_job is None:
            self._schedule_drain()
        self._start_thread()
        logger.info("Caregiver link (re)connected → %s", self.api.base_url)

    def _check_stalled(self) -> None:
        """Watchdog (Tk thread). Every call has a 5 s timeout, so the worker
        should always make progress; if it hasn't for STALL_S seconds, say so
        and start a fresh thread with a fresh HTTP session."""
        if not self._thread or self._stop.is_set():
            return
        stuck_for = time.monotonic() - self._last_progress
        if stuck_for < self.STALL_S:
            return
        logger.error("Caregiver link: background thread made no progress for %.0f s — restarting it", stuck_for)
        self._events.put(("status", (False, "Caregiver board: not responding, reconnecting")))
        self._connected = None
        old_api = self.api
        self.api = CaregiverAPI(old_api.base_url, old_api.token, old_api.timeout)
        self._start_thread()

    def stop(self) -> None:
        """Stop the thread. Unsent normal requests are dropped; this is logged."""
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=self.api.timeout + 1)
        if self._drain_job is not None:
            try:
                self.root.after_cancel(self._drain_job)
            except tk.TclError:
                pass
            self._drain_job = None
        with self._outbox_lock:
            if self._outbox:
                logger.warning("Caregiver link stopped with %d unsent request(s)", len(self._outbox))
        self.api.close()

    # -- used by the app (Tk thread, never blocks) ------------------------

    def submit_request(self, phrase: str, is_emergency: bool = False,
                       pain_level: Optional[int] = None) -> None:
        """Queue a phrase / SOS / pain level for the dashboard. Returns immediately."""
        if not self.enabled or not phrase.strip():
            return
        item = _Outgoing(phrase, is_emergency, pain_level, time.time())
        with self._outbox_lock:
            if is_emergency:
                # SOS goes out before anything else still waiting.
                self._outbox.appendleft(item)
            else:
                self._outbox.append(item)
            if len(self._outbox) > self.MAX_OUTBOX:
                self._drop_oldest_normal()
        self._wake.set()

    # -- worker thread ----------------------------------------------------

    def _run(self, generation: int) -> None:
        next_heartbeat = 0.0
        while not self._stop.is_set() and generation == self._generation:
            now = time.monotonic()
            self._last_progress = now
            wait = 1.0
            try:
                self._flush_outbox()

                if now >= next_heartbeat:
                    beat = self.api.send_heartbeat()
                    name = (beat.get("patient") or {}).get("name", "patient")
                    self._set_connected(True, f"Caregiver board connected ({name})")
                    next_heartbeat = now + self.HEARTBEAT_S
                    if beat.get("pending_messages"):
                        self._fetch_messages()

                self._poll_watches()
                self._backoff = 0.0

                upcoming = [next_heartbeat] + [w.next_check for w in self._watches.values()]
                wait = max(0.2, min(upcoming) - time.monotonic())

            except TokenRejectedError as exc:
                logger.error("Caregiver link: %s", exc)
                self._set_connected(False, "Caregiver board: disconnected — press Ctrl+Shift+P to connect")
                wait = self.TOKEN_RETRY_S
                next_heartbeat = time.monotonic() + wait

            except CaregiverAPIError as exc:
                self._backoff = min(max(self._backoff * 2, 2.0), self.MAX_BACKOFF_S)
                if self._connected is not False:
                    logger.warning("Caregiver link: %s — retrying (next try in %.0f s)", exc, self._backoff)
                self._set_connected(False, "Caregiver board: offline, retrying")
                wait = self._backoff
                next_heartbeat = time.monotonic() + wait

            except Exception:  # never let the thread die
                logger.exception("Caregiver link: unexpected error")
                wait = 5.0

            self._wake.wait(timeout=wait)
            self._wake.clear()

    def _flush_outbox(self) -> None:
        """Send queued requests in order. Stops (raises) at the first failure,
        leaving that request and the rest queued for the next try."""
        while not self._stop.is_set():
            with self._outbox_lock:
                if not self._outbox:
                    return
                item = self._outbox[0]
            try:
                created = self.api.send_request(item.phrase, item.is_emergency, item.pain_level)
            except CaregiverAPIError as exc:
                if exc.status == 400:
                    # The dashboard refused the data itself; retrying won't help.
                    logger.error("Caregiver link: dropped invalid request %r: %s", item.phrase, exc)
                    self._pop_outbox(item)
                    continue
                raise
            self._pop_outbox(item)
            self._last_progress = time.monotonic()
            delay = time.time() - item.queued_at
            if delay > 10:
                logger.info("Caregiver link: delivered %r after %.0f s offline", item.phrase, delay)
            self._watch(created)
            self._events.put(("sent", created))

    def _pop_outbox(self, item: _Outgoing) -> None:
        with self._outbox_lock:
            if self._outbox and self._outbox[0] is item:
                self._outbox.popleft()
            else:
                try:
                    self._outbox.remove(item)
                except ValueError:
                    pass

    def _drop_oldest_normal(self) -> None:
        """Called with the outbox lock held: make room without ever dropping an SOS."""
        for i, queued in enumerate(self._outbox):
            if not queued.is_emergency:
                del self._outbox[i]
                logger.warning("Caregiver link: outbox full, dropped oldest request %r", queued.phrase)
                return

    def _watch(self, created: dict) -> None:
        request_id = created.get("id")
        if request_id is None or created.get("acknowledged"):
            return
        now = time.monotonic()
        if created.get("is_emergency"):
            self._watches[request_id] = _Watch(True, now, now + self.SOS_POLL_S)
        elif self.watch_normal_requests:
            normal = [rid for rid, w in self._watches.items() if not w.is_emergency]
            for rid in normal[: max(0, len(normal) - self.MAX_NORMAL_WATCHES + 1)]:
                del self._watches[rid]
            self._watches[request_id] = _Watch(False, now, now + self.NORMAL_POLL_S)

    def _poll_watches(self) -> None:
        now = time.monotonic()
        for request_id, watch in list(self._watches.items()):
            limit = self.SOS_WATCH_MAX_S if watch.is_emergency else self.NORMAL_WATCH_MAX_S
            if now - watch.started > limit:
                del self._watches[request_id]
                continue
            if now < watch.next_check:
                continue
            try:
                status = self.api.check_ack(request_id)
            except CaregiverAPIError as exc:
                if exc.status == 404:  # deleted on the dashboard
                    del self._watches[request_id]
                    continue
                raise
            if status.get("acknowledged"):
                del self._watches[request_id]
                self._events.put(("ack", status))
            else:
                watch.next_check = now + (self.SOS_POLL_S if watch.is_emergency else self.NORMAL_POLL_S)

    def _fetch_messages(self) -> None:
        for message in self.api.get_messages():
            self._events.put(("message", message))

    def _set_connected(self, connected: bool, text: str) -> None:
        if connected != self._connected:
            self._connected = connected
            if connected:
                logger.info("Caregiver link: connected to %s", self.api.base_url)
            self._events.put(("status", (connected, text)))

    # -- hand results to the Tk thread -------------------------------------

    def _schedule_drain(self) -> None:
        try:
            self._drain_job = self.root.after(self.DRAIN_MS, self._drain)
        except (tk.TclError, RuntimeError):
            self._drain_job = None  # window already closed

    def _drain(self) -> None:
        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                break
            callback = {
                "status": self.on_status,
                "ack": self.on_ack,
                "message": self.on_message,
                "sent": self.on_sent,
            }.get(kind)
            if callback is None:
                continue
            try:
                if kind == "status":
                    callback(*payload)
                else:
                    callback(payload)
            except Exception:
                logger.exception("Caregiver link: %s callback failed", kind)
        if not self._stop.is_set():
            self._check_stalled()
            self._schedule_drain()


# ══════════════════════════════════════════════════════════════════════
# 4. NoticeOverlay — big notices for the patient + connection badge
# ══════════════════════════════════════════════════════════════════════

class NoticeOverlay:
    """Large, high-contrast notices drawn on top of the main window.

    The patient cannot click, so notices hide themselves after a while.
    """

    STYLES = {
        # kind: (background, text colour, border)
        "help": ("#15803D", "#FFFFFF", "#BBF7D0"),     # green: help is coming
        "message": ("#1D4ED8", "#FFFFFF", "#BFDBFE"),  # blue: caregiver message
        "info": ("#334155", "#FFFFFF", "#CBD5E1"),
    }

    def __init__(self, root: tk.Misc, font_family: str = "Segoe UI"):
        self.root = root
        self.font = font_family
        self._frame: Optional[tk.Frame] = None
        self._hide_job: Optional[str] = None
        self._badge = tk.Label(
            root, text="", font=(font_family, 10, "bold"),
            fg="#CBD5E1", bg="#1E293B", padx=10, pady=4,
        )

    def show(self, title: str, body: str = "", kind: str = "info", seconds: float = 20.0) -> None:
        """Show a notice near the top centre of the window, replacing any current one."""
        self.hide()
        bg, fg, border = self.STYLES.get(kind, self.STYLES["info"])
        width = max(self.root.winfo_width(), 800)
        wrap = int(width * 0.7)

        frame = tk.Frame(self.root, bg=bg, padx=48, pady=28,
                         highlightthickness=4, highlightbackground=border)
        tk.Label(frame, text=title, font=(self.font, 34, "bold"), fg=fg, bg=bg,
                 wraplength=wrap, justify="center").pack()
        if body:
            tk.Label(frame, text=body, font=(self.font, 24), fg=fg, bg=bg,
                     wraplength=wrap, justify="center").pack(pady=(14, 0))
        frame.place(relx=0.5, rely=0.12, anchor="n")
        frame.lift()
        self._frame = frame
        self._hide_job = self.root.after(int(seconds * 1000), self.hide)

    def hide(self) -> None:
        if self._hide_job is not None:
            try:
                self.root.after_cancel(self._hide_job)
            except tk.TclError:
                pass
            self._hide_job = None
        if self._frame is not None:
            try:
                self._frame.destroy()
            except tk.TclError:
                pass
            self._frame = None

    def set_status(self, connected: bool, text: str) -> None:
        """Small badge in the bottom-left corner showing the dashboard connection."""
        dot = "●"
        self._badge.config(text=f"{dot} {text}", fg="#86EFAC" if connected else "#FCD34D")
        self._badge.place(relx=0.0, rely=1.0, x=12, y=-10, anchor="sw")
        self._badge.lift()


# ══════════════════════════════════════════════════════════════════════
# 5. PairingWindow — "Connect to caregiver dashboard" (typed by a caregiver)
# ══════════════════════════════════════════════════════════════════════

class PairingWindow:
    """Small window where a caregiver types the dashboard's 6-digit code.

    The network call runs on a background thread, so the window never freezes.
    on_paired(api) runs on the Tk thread with a ready CaregiverAPI.
    """

    BG = "#0F172A"
    CARD = "#1E293B"
    TEXT = "#F1F5F9"
    MUTED = "#94A3B8"
    ACCENT = "#3B82F6"

    def __init__(self, root: tk.Misc, on_paired: Callable[[CaregiverAPI], None],
                 on_skip: Optional[Callable[[], None]] = None, font_family: str = "Segoe UI"):
        self.root = root
        self.on_paired = on_paired
        self.on_skip = on_skip
        self.font = font_family
        self._busy = False
        self._result: "queue.Queue[tuple[str, Any]]" = queue.Queue()

        win = self.win = tk.Toplevel(root)
        win.title("Connect to caregiver dashboard")
        win.configure(bg=self.BG, padx=36, pady=28)
        win.resizable(False, False)
        win.transient(root)
        win.protocol("WM_DELETE_WINDOW", self._skip)

        tk.Label(win, text="Connect to caregiver dashboard", font=(font_family, 20, "bold"),
                 fg=self.TEXT, bg=self.BG).pack(anchor="w")
        tk.Label(win, text="On the dashboard, open the patient and click \"Connect gaze app\".\n"
                           "Then type the 6-digit code shown there.",
                 font=(font_family, 12), fg=self.MUTED, bg=self.BG, justify="left").pack(anchor="w", pady=(4, 18))

        tk.Label(win, text="Dashboard address", font=(font_family, 11, "bold"), fg=self.TEXT, bg=self.BG).pack(anchor="w")
        self.url_var = tk.StringVar(value=default_dashboard_url())
        tk.Entry(win, textvariable=self.url_var, font=(font_family, 14), width=34,
                 relief="flat", bg=self.CARD, fg=self.TEXT, insertbackground=self.TEXT).pack(anchor="w", ipady=6, pady=(4, 14))

        tk.Label(win, text="Connection code", font=(font_family, 11, "bold"), fg=self.TEXT, bg=self.BG).pack(anchor="w")
        self.code_var = tk.StringVar()
        self.code_entry = tk.Entry(win, textvariable=self.code_var, font=(font_family, 28, "bold"), width=9,
                                   justify="center", relief="flat", bg=self.CARD, fg=self.TEXT,
                                   insertbackground=self.TEXT)
        self.code_entry.pack(anchor="w", ipady=6, pady=(4, 6))

        self.status = tk.Label(win, text="", font=(font_family, 12), fg=self.MUTED, bg=self.BG,
                               wraplength=460, justify="left")
        self.status.pack(anchor="w", pady=(4, 16))

        buttons = tk.Frame(win, bg=self.BG)
        buttons.pack(fill="x")
        self.connect_btn = tk.Button(buttons, text="Connect", font=(font_family, 13, "bold"), width=12,
                                     bg=self.ACCENT, fg="white", activebackground="#2563EB",
                                     activeforeground="white", relief="flat", command=self._connect)
        self.connect_btn.pack(side="left")
        tk.Button(buttons, text="Skip for now", font=(font_family, 12), relief="flat", bg=self.CARD,
                  fg=self.TEXT, activebackground="#334155", activeforeground=self.TEXT,
                  command=self._skip).pack(side="left", padx=10)

        win.bind("<Return>", lambda _e: self._connect())
        win.bind("<Escape>", lambda _e: self._skip())
        win.update_idletasks()
        x = root.winfo_rootx() + max(0, (root.winfo_width() - win.winfo_width()) // 2)
        y = root.winfo_rooty() + max(0, (root.winfo_height() - win.winfo_height()) // 3)
        win.geometry(f"+{x}+{y}")
        win.lift()
        win.attributes("-topmost", True)
        win.after(300, lambda: win.attributes("-topmost", False))
        self.code_entry.focus_force()
        try:
            win.grab_set()
        except tk.TclError:
            pass

    def _say(self, text: str, color: Optional[str] = None) -> None:
        self.status.config(text=text, fg=color or self.MUTED)

    def _connect(self) -> None:
        if self._busy:
            return
        code = "".join(ch for ch in self.code_var.get() if ch.isdigit())
        if len(code) != 6:
            self._say("Type the 6-digit code from the dashboard.", "#FCA5A5")
            return
        url = self.url_var.get().strip()
        self._busy = True
        self.connect_btn.config(state="disabled", text="Connecting…")
        self._say("Connecting…")
        threading.Thread(target=self._pair_worker, args=(url, code), daemon=True).start()
        self.win.after(100, self._poll_result)

    def _pair_worker(self, url: str, code: str) -> None:
        try:
            data = pair_device(url, code)
            device = socket.gethostname()
            save_link(data["base_url"], data["token"], data["patient"]["name"], device)
            self._result.put(("ok", data))
        except CaregiverAPIError as exc:
            self._result.put(("error", str(exc)))
        except Exception as exc:
            logger.exception("Pairing failed")
            self._result.put(("error", f"Could not connect: {exc}"))

    def _poll_result(self) -> None:
        try:
            kind, payload = self._result.get_nowait()
        except queue.Empty:
            self.win.after(100, self._poll_result)
            return
        self._busy = False
        self.connect_btn.config(state="normal", text="Connect")
        if kind == "error":
            self._say(payload, "#FCA5A5")
            self.code_entry.focus_set()
            return
        name = payload["patient"]["name"]
        self._say(f"Connected to {name}'s caregiver dashboard.", "#86EFAC")
        logger.info("Paired with the caregiver dashboard as %s", name)
        api = CaregiverAPI(payload["base_url"], payload["token"])
        self.win.after(1200, lambda: self._close(api))

    def _close(self, api: Optional[CaregiverAPI]) -> None:
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        self.win.destroy()
        if api is not None:
            self.on_paired(api)

    def _skip(self) -> None:
        if self._busy:
            return
        self._close(None)
        if self.on_skip:
            self.on_skip()


# ══════════════════════════════════════════════════════════════════════
# Quick check from the command line:  python caregiver_link.py
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    api = CaregiverAPI()
    if not api.configured:
        raise SystemExit("Not connected yet: start GazeAssist and press Ctrl+Shift+P to enter the dashboard's code.")
    print("Dashboard:", api.base_url)
    try:
        beat = api.send_heartbeat()
        print("Heartbeat OK — patient:", beat["patient"]["name"],
              "| pending messages:", beat["pending_messages"])
        sent = api.send_request("Test request from caregiver_link.py")
        print("Request sent — id", sent["id"], "|", sent["message"])
        print("Status:", api.check_ack(sent["id"])["message"])
        print("Messages:", [m["text"] for m in api.get_messages()] or "none")
    except CaregiverAPIError as exc:
        raise SystemExit(f"FAILED: {exc}")
