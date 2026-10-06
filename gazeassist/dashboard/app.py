"""
GazeAssist - Flask Caregiver Dashboard

Serves on the local network (0.0.0.0:5000).  Displays session communication log,
pain timeline, most-used phrases, SOS events, and session stats.
Updates via JavaScript polling every 3 seconds.
"""

import json
import logging
import threading
from datetime import datetime

from flask import Flask, render_template, jsonify

logger = logging.getLogger(__name__)

_app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)

# Shared reference to the database — set by start_dashboard()
_db = None
_session_id = None
_sos_flag = threading.Event()


def set_sos_flag():
    """Set the SOS flag (called by alert system)."""
    _sos_flag.set()


def clear_sos_flag():
    """Clear the SOS flag after caregiver acknowledges."""
    _sos_flag.clear()


# ── Routes ────────────────────────────────────────────────────────────

@_app.route("/")
def index():
    return render_template("index.html")


@_app.route("/api/session-log")
def api_session_log():
    if _db and _session_id:
        log = _db.get_session_log(_session_id)
        return jsonify(log)
    return jsonify([])


@_app.route("/api/pain-timeline")
def api_pain_timeline():
    if _db and _session_id:
        history = _db.get_pain_history(_session_id)
        return jsonify(history)
    return jsonify([])


@_app.route("/api/phrases")
def api_phrases():
    if _db and _session_id:
        phrases = _db.get_most_used_phrases(_session_id, limit=10)
        return jsonify(phrases)
    return jsonify([])


@_app.route("/api/sos-events")
def api_sos_events():
    if _db and _session_id:
        events = _db.get_sos_events(_session_id)
        return jsonify(events)
    return jsonify([])


@_app.route("/api/stats")
def api_stats():
    if _db and _session_id:
        stats = _db.get_session_stats(_session_id)
        return jsonify(stats)
    return jsonify({})


@_app.route("/api/poll")
def api_poll():
    """Lightweight poll endpoint for real-time updates."""
    sos_active = _sos_flag.is_set()
    data = {
        "timestamp": datetime.now().isoformat(),
        "sos_active": sos_active,
        "session_id": _session_id,
    }
    return jsonify(data)


@_app.route("/api/sos-acknowledge", methods=["POST"])
def api_sos_ack():
    """Acknowledge SOS alert from the dashboard."""
    clear_sos_flag()
    return jsonify({"status": "acknowledged"})


# ── Dashboard server ──────────────────────────────────────────────────

def start_dashboard(db, session_id: int, host: str = "0.0.0.0", port: int = 5000):
    """
    Start the Flask dashboard in a daemon thread.

    Args:
        db: Database instance.
        session_id: Current session ID.
        host: Bind address.
        port: Port number.
    """
    global _db, _session_id
    _db = db
    _session_id = session_id

    def run():
        # Suppress Flask's default logging noise
        flask_log = logging.getLogger("werkzeug")
        flask_log.setLevel(logging.WARNING)

        _app.run(host=host, port=port, debug=False, use_reloader=False)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    logger.info("Dashboard started at http://%s:%d", host, port)
    return thread


def update_session_id(session_id: int):
    """Update the active session ID."""
    global _session_id
    _session_id = session_id
