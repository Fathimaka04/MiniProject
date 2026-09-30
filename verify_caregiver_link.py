"""
Verify the link between this gaze app and the caregiver web dashboard.

No camera needed. Opens a small Tk window and:
  1. connects (heartbeats every 5 s -> patient shows Online on the dashboard)
  2. sends a normal request and an SOS
  3. waits for YOU to press Acknowledge on the dashboard and to send a
     message to the patient from the dashboard
  4. checks the Tk thread was never blocked (network runs in the background)

Before running:
  - start the dashboard:   cd ..\\gazeassist_web ; python manage.py runserver
  - put GAZEASSIST_API_URL and GAZEASSIST_API_TOKEN in this project's .env

Run:
  python verify_caregiver_link.py
"""
import logging
import sys
import time
import tkinter as tk

from dotenv import load_dotenv

load_dotenv()

from caregiver_link import CaregiverAPI, CaregiverLink, NoticeOverlay  # noqa: E402

TIMEOUT_S = 180
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

api = CaregiverAPI()
if not api.configured:
    sys.exit("GAZEASSIST_API_TOKEN is not set in .env — see the instructions at the top of this file.")

root = tk.Tk()
root.title("GazeAssist — caregiver link check")
root.geometry("1000x620")
root.configure(bg="#0F172A")
info = tk.Label(root, text="Connecting to the caregiver dashboard…", font=("Segoe UI", 16),
                fg="#F1F5F9", bg="#0F172A", wraplength=900, justify="left")
info.pack(side="bottom", pady=40)

notices = NoticeOverlay(root)
state = {"connected": False, "sent": [], "sos_id": None, "ack": None, "message": None, "max_gap": 0.0}


def say(text):
    print(text)
    info.config(text=text)


def on_status(connected, text):
    notices.set_status(connected, text)
    state["connected"] = connected
    print(f"[status] {text}")


def on_sent(created):
    state["sent"].append(created)
    kind = "SOS" if created["is_emergency"] else "request"
    print(f"[sent] {kind} #{created['id']}: {created['phrase']!r} -> {created['message']}")
    if created["is_emergency"]:
        state["sos_id"] = created["id"]
        say("SOS sent. Now, on the dashboard:\n"
            "  1) press Acknowledge on the red banner\n"
            "  2) open the patient → Message → send any message")


def on_ack(status):
    print(f"[ack] #{status['id']} by {status['acknowledged_by']}: {status['message']}")
    if status["is_emergency"]:
        state["ack"] = status
        notices.show("Help is on the way", f"{status['acknowledged_by']} saw your SOS.", kind="help", seconds=8)


def on_message(message):
    print(f"[message] from {message['sender']}: {message['text']}")
    state["message"] = message
    notices.show(f"Message from {message['sender']}", message["text"], kind="message", seconds=8)


link = CaregiverLink(root, api=api, on_status=on_status, on_ack=on_ack,
                     on_message=on_message, on_sent=on_sent)

# Measure how long the Tk thread goes between ticks (should stay ~50 ms).
last_tick = [time.monotonic()]
started = time.monotonic()


def tick():
    now = time.monotonic()
    state["max_gap"] = max(state["max_gap"], now - last_tick[0])
    last_tick[0] = now
    elapsed = now - started
    if state["ack"] and state["message"]:
        root.after(1500, finish)
        return
    if elapsed > TIMEOUT_S:
        finish()
        return
    root.after(50, tick)


def send_test_requests():
    link.submit_request("Test: I am thirsty")
    link.submit_request("Test: SOS – I need help now", is_emergency=True)


def finish():
    link.stop()
    ok_conn = bool(state["sent"])
    ok_ack = state["ack"] is not None
    ok_msg = state["message"] is not None
    ok_ui = state["max_gap"] < 0.5
    print("\n──────── Result ────────")
    print(f"Connected & sent requests : {'OK' if ok_conn else 'FAILED'}")
    print(f"SOS acknowledged          : {'OK' if ok_ack else 'not seen'}")
    print(f"Caregiver message received: {'OK' if ok_msg else 'not seen'}")
    print(f"UI never froze (max gap)  : {'OK' if ok_ui else 'FAILED'} ({state['max_gap'] * 1000:.0f} ms)")
    root.destroy()
    sys.exit(0 if (ok_conn and ok_ack and ok_msg and ok_ui) else 1)


link.start()
root.after(1500, send_test_requests)
root.after(50, tick)
root.protocol("WM_DELETE_WINDOW", finish)
root.mainloop()
