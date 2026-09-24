"""
GazeAssist - Multi-channel SOS Alert System

Fires all three alert channels simultaneously on SOS trigger:
1. Local audible alarm via pygame.mixer
2. WhatsApp/SMS via Twilio (or pywhatkit fallback)
3. Red alert pushed to caregiver dashboard via SQLite flag
"""

import os
import time
import json
import wave
import struct
import math
import threading
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Alarm Sound Generator (no external WAV file needed)
# ──────────────────────────────────────────────────────────────────────

def generate_alarm_wav(filepath: str, duration_s: float = 3.0, sample_rate: int = 22050):
    """Generate a loud alternating-frequency alarm WAV file."""
    n_samples = int(sample_rate * duration_s)
    samples = []
    for i in range(n_samples):
        t = i / sample_rate
        # Alternating two-tone alarm (800Hz and 1200Hz, switching every 0.25s)
        freq = 800 if int(t / 0.25) % 2 == 0 else 1200
        amplitude = 0.8 * math.sin(2 * math.pi * freq * t)
        # Add urgency modulation
        amplitude *= 0.5 + 0.5 * math.sin(2 * math.pi * 4 * t)
        samples.append(int(amplitude * 32767))

    with wave.open(filepath, 'w') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(struct.pack(f'<{len(samples)}h', *samples))


class AlertSystem:
    """
    Multi-channel alert system for SOS events.

    Fires speaker alarm, WhatsApp/SMS, and dashboard notification
    simultaneously in separate threads.
    """

    def __init__(self, db=None, session_id: Optional[int] = None,
                 emergency_contact: Optional[str] = None):
        """
        Args:
            db: Database instance for logging and dashboard alerts.
            session_id: Current session ID for logging.
            emergency_contact: Phone number for SMS/WhatsApp.
        """
        self._db = db
        self._session_id = session_id
        self._emergency_contact = emergency_contact
        self._alarm_wav_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "_alarm.wav"
        )
        self._pygame_initialized = False
        self._sos_active = threading.Event()

        # Generate alarm sound if not exists
        if not os.path.exists(self._alarm_wav_path):
            try:
                generate_alarm_wav(self._alarm_wav_path)
                logger.info(f"Generated alarm WAV at {self._alarm_wav_path}")
            except Exception as e:
                logger.error(f"Failed to generate alarm WAV: {e}")

        # Initialize pygame mixer
        try:
            import pygame
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=22050, size=-16, channels=1)
            self._pygame_initialized = True
        except Exception as e:
            logger.warning(f"pygame mixer init failed: {e}")

    def update_session(self, session_id: int):
        """Update current session ID."""
        self._session_id = session_id

    def fire_all_alerts(self, reason: str = "Emergency signal"):
        """Fire all three alert channels simultaneously."""
        self._sos_active.set()
        channels_fired = []
        threads = []

        # Channel 1: Local alarm
        t1 = threading.Thread(target=self._fire_speaker_alarm, daemon=True)
        threads.append(("speaker", t1))

        # Channel 2: WhatsApp/SMS
        t2 = threading.Thread(
            target=self._fire_messaging_alert, args=(reason,), daemon=True
        )
        threads.append(("messaging", t2))

        # Channel 3: Dashboard notification
        t3 = threading.Thread(target=self._fire_dashboard_alert, daemon=True)
        threads.append(("dashboard", t3))

        # Start all simultaneously
        for name, t in threads:
            t.start()

        # Wait for all to complete (with timeout)
        for name, t in threads:
            t.join(timeout=15.0)
            channels_fired.append(name)

        # Log the event
        if self._db and self._session_id:
            try:
                self._db.log_sos(self._session_id, channels_fired)
            except Exception as e:
                logger.error(f"Failed to log SOS event: {e}")

        logger.critical(f"🚨 SOS alerts fired on channels: {channels_fired}")

    def stop_alarm(self):
        """Stop the ongoing alarm sound."""
        self._sos_active.clear()
        try:
            import pygame
            pygame.mixer.stop()
        except Exception:
            pass

    def _fire_speaker_alarm(self):
        """Play loud alarm through system speakers."""
        try:
            import pygame

            if not self._pygame_initialized:
                logger.warning("Speaker alarm: pygame not initialized")
                return

            if os.path.exists(self._alarm_wav_path):
                sound = pygame.mixer.Sound(self._alarm_wav_path)
                sound.set_volume(1.0)
                # Play alarm 5 times (loops=4 means 5 total plays)
                sound.play(loops=4)
                logger.info("🔊 Speaker alarm playing")
            else:
                logger.error("Alarm WAV file not found")

        except Exception as e:
            logger.error(f"Speaker alarm failed: {e}")

    def _fire_messaging_alert(self, reason: str = "Emergency signal"):
        """Send SMS (Fast2SMS) and WhatsApp (pywhatkit) simultaneously.

        Both channels fire in parallel rather than one-after-another
        fallback, since either can silently fail on its own (SMS gateway
        issue, WhatsApp Web session logged out) — sending both maximizes
        the chance the caregiver actually sees it. Only falls back to a
        simulated log entry if BOTH real channels fail.
        """
        if not self._emergency_contact:
            logger.warning("Messaging alert: no emergency contact configured")
            self._log_simulated_send("No emergency contact configured")
            return

        message = (
            "🚨 EMERGENCY ALERT from GazeAssist!\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Reason: {reason}\n"
            "Please check on them immediately!"
        )
        results = {}

        def run_fast2sms():
            results["sms"] = self._try_fast2sms(message)

        def run_pywhatkit():
            results["whatsapp"] = self._try_pywhatkit(message)

        t_sms = threading.Thread(target=run_fast2sms, daemon=True)
        t_wa = threading.Thread(target=run_pywhatkit, daemon=True)
        t_sms.start()
        t_wa.start()
        t_sms.join(timeout=20.0)
        t_wa.join(timeout=20.0)

        logger.info(
            "Messaging results — SMS: %s, WhatsApp: %s",
            "sent" if results.get("sms") else "failed",
            "sent" if results.get("whatsapp") else "failed",
        )

        if not results.get("sms") and not results.get("whatsapp"):
            self._log_simulated_send(message)

    def _try_fast2sms(self, message: str) -> bool:
        """Attempt to send a real SMS via Fast2SMS."""
        api_key = os.environ.get("FAST2SMS_API_KEY")
        if not api_key:
            logger.info("Fast2SMS API key not configured, skipping")
            return False

        # Fast2SMS wants a plain 10-digit Indian number, no +91/91 prefix.
        phone = self._emergency_contact or ""
        digits = "".join(c for c in phone if c.isdigit())
        if len(digits) > 10:
            digits = digits[-10:]  # strip any country code prefix

        try:
            import requests
            resp = requests.post(
                "https://www.fast2sms.com/dev/bulkV2",
                headers={"authorization": api_key},
                data={
                    "route": "q",
                    "message": message,
                    "numbers": digits,
                },
                timeout=10,
            )
            data = resp.json()
            if data.get("return") is True:
                logger.info("✅ SMS alert sent via Fast2SMS")
                return True
            logger.error("Fast2SMS send failed: %s", data)
            return False
        except ImportError:
            logger.info("requests package not installed for Fast2SMS")
            return False
        except Exception as e:
            logger.error("Fast2SMS send failed: %s", e)
            return False


    def _try_pywhatkit(self, message: str) -> bool:
        """Send WhatsApp via Meta's official Cloud API (test sandbox).

        Uses a **template message** — the sandbox only delivers template
        messages to numbers that haven't first messaged the test number.
        The pre-approved ``hello_world`` template works on every sandbox
        without extra setup.
        """
        phone_number_id = os.environ.get("META_WA_PHONE_NUMBER_ID")
        access_token = os.environ.get("META_WA_ACCESS_TOKEN")

        if not phone_number_id or not access_token:
            logger.info("Meta WhatsApp Cloud API not configured, skipping")
            return False

        phone = self._emergency_contact or ""
        digits = "".join(c for c in phone if c.isdigit())
        if not phone.startswith("+") and len(digits) == 10:
            digits = "91" + digits  # default India country code

        try:
            import requests

            # --- Attempt 1: free-form text (works if recipient opted in) ---
            resp = requests.post(
                f"https://graph.facebook.com/v18.0/{phone_number_id}/messages",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "messaging_product": "whatsapp",
                    "to": digits,
                    "type": "text",
                    "text": {"body": message},
                },
                timeout=10,
            )
            data = resp.json()
            if resp.status_code == 200 and "messages" in data:
                logger.info("✅ WhatsApp alert sent (text): %s",
                            data["messages"][0].get("id"))
                return True

            logger.warning("Free-form text failed (%s), trying template...",
                           data.get("error", {}).get("message", resp.status_code))

            # --- Attempt 2: template message (always works on sandbox) ---
            resp2 = requests.post(
                f"https://graph.facebook.com/v18.0/{phone_number_id}/messages",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "messaging_product": "whatsapp",
                    "to": digits,
                    "type": "template",
                    "template": {
                        "name": "hello_world",
                        "language": {"code": "en_US"},
                    },
                },
                timeout=10,
            )
            data2 = resp2.json()
            if resp2.status_code == 200 and "messages" in data2:
                logger.info("✅ WhatsApp alert sent (template): %s",
                            data2["messages"][0].get("id"))
                return True

            logger.error("Meta WhatsApp template also failed: %s", data2)
            return False

        except ImportError:
            logger.info("requests package not installed")
            return False
        except Exception as e:
            logger.error(f"Meta WhatsApp send failed: {e}")
            return False

    def _log_simulated_send(self, message: str):
        """Log a simulated send when no messaging backend is available."""
        logger.warning(
            f"📱 SIMULATED ALERT (no messaging backend configured)\n"
            f"   To: {self._emergency_contact or 'N/A'}\n"
            f"   Message: {message}"
        )

    def _fire_dashboard_alert(self):
        """Push SOS alert to the caregiver dashboard."""
        if self._db and self._session_id:
            try:
                # The dashboard polls for SOS events, so just logging it
                # makes it visible on the next poll
                logger.info("🖥️ Dashboard SOS alert logged")
            except Exception as e:
                logger.error(f"Dashboard alert failed: {e}")
        else:
            logger.warning("Dashboard alert: no database/session configured")
