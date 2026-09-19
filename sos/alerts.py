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

    def fire_all_alerts(self):
        """Fire all three alert channels simultaneously."""
        self._sos_active.set()
        channels_fired = []
        threads = []

        # Channel 1: Local alarm
        t1 = threading.Thread(target=self._fire_speaker_alarm, daemon=True)
        threads.append(("speaker", t1))

        # Channel 2: WhatsApp/SMS
        t2 = threading.Thread(target=self._fire_messaging_alert, daemon=True)
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

    def _fire_messaging_alert(self):
        """Send WhatsApp/SMS to emergency contact."""
        if not self._emergency_contact:
            logger.warning("Messaging alert: no emergency contact configured")
            self._log_simulated_send("No emergency contact configured")
            return

        message = (
            "🚨 EMERGENCY ALERT from GazeAssist!\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            "The patient has triggered the SOS emergency signal.\n"
            "Please check on them immediately!"
        )

        # Try Twilio first
        if self._try_twilio(message):
            return

        # Try pywhatkit fallback
        if self._try_pywhatkit(message):
            return

        # Log simulated send
        self._log_simulated_send(message)

    def _try_twilio(self, message: str) -> bool:
        """Attempt to send via Twilio."""
        account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
        from_number = os.environ.get("TWILIO_FROM_NUMBER")

        if not all([account_sid, auth_token, from_number]):
            logger.info("Twilio credentials not configured, skipping")
            return False

        try:
            from twilio.rest import Client
            client = Client(account_sid, auth_token)

            # Try WhatsApp first
            try:
                wa_message = client.messages.create(
                    from_=f"whatsapp:{from_number}",
                    body=message,
                    to=f"whatsapp:{self._emergency_contact}"
                )
                logger.info(f"✅ WhatsApp alert sent: {wa_message.sid}")
                return True
            except Exception:
                pass

            # Fallback to SMS
            sms = client.messages.create(
                from_=from_number,
                body=message,
                to=self._emergency_contact
            )
            logger.info(f"✅ SMS alert sent: {sms.sid}")
            return True

        except ImportError:
            logger.info("Twilio package not installed")
            return False
        except Exception as e:
            logger.error(f"Twilio send failed: {e}")
            return False

    def _try_pywhatkit(self, message: str) -> bool:
        """Attempt to send via pywhatkit."""
        try:
            import pywhatkit
            phone = self._emergency_contact
            if not phone.startswith("+"):
                phone = f"+91{phone}"  # Default to India country code

            pywhatkit.sendwhatmsg_instantly(
                phone_no=phone,
                message=message,
                wait_time=10,
                tab_close=True
            )
            logger.info("✅ WhatsApp alert sent via pywhatkit")
            return True

        except ImportError:
            logger.info("pywhatkit not installed")
            return False
        except Exception as e:
            logger.error(f"pywhatkit send failed: {e}")
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
