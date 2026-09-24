"""
GazeAssist — Main Application Entry Point

A single command launches the full system:
  python main.py

Flow:
  1. Check for existing user profile → first-run wizard if none
  2. Start shared perception layer (Thread 1)
  3. Start Flask dashboard (Thread 4 — daemon)
  4. Start SOS background monitor (Thread 3)
  5. Run gaze calibration if needed
  6. Run blink enrollment if needed
  7. Initialize state machine in Navigate mode
  8. Launch Tkinter main window with phrase board (Thread 2 — main thread)
"""

import os
import sys
import time
import logging
import tkinter as tk
from collections import deque, Counter
from typing import Optional

# ── Load .env before anything reads os.environ ────────────────────────
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("gazeassist")

# ── Resolve project root for imports ──────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── Imports ───────────────────────────────────────────────────────────
from db.models import Database
from perception.face_mesh import SharedPerception
from blink.ear import compute_both_ears
from blink.classifier import BlinkClassifier
from blink.enrollment import BlinkEnrollment
from gaze.features import extract_gaze_features, weight_gaze_features
from gaze.model import GazePredictor
from gaze.calibration import CalibrationScreen
from phrase_board.tiles import Tile, get_tile_text
from phrase_board.confirm import SelectionConfirmer
from phrase_board.frequency import TileReorderer
from phrase_board.ui import PhraseBoardUI
from pain_scale.ui import PainScaleUI
from sos.monitor import SOSMonitor
from sos.alerts import AlertSystem
from state_machine.modes import StateMachine, AppMode
from tts.indic_tts import create_tts_engine
from dashboard.app import start_dashboard, set_sos_flag
from calibration_screens.setup_wizard import SetupWizard

# ── Constants ─────────────────────────────────────────────────────────
DB_PATH = os.path.join(PROJECT_ROOT, "gazeassist.db")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
FRAME_UPDATE_MS = 33  # ~30 Hz UI update rate
EYES_CLOSING_EAR = 0.21  # matches blink/classifier.py's ear_threshold


class GazeAssistApp:
    """
    Main application class — orchestrates all six modules.
    """

    def __init__(self):
        # Core components
        self.db = Database(DB_PATH)
        self.perception = SharedPerception(camera_index=0)
        self.tts = create_tts_engine()
        self.state_machine = StateMachine(AppMode.SETUP)
        self.gaze_predictor = GazePredictor()
        self._zone_history = deque(maxlen=10)
        self._locked_zone = -1
        self.blink_classifier = BlinkClassifier()

        # User state
        self.user_id: Optional[int] = None
        self.session_id: Optional[int] = None
        self.language: str = "hi"
        self.emergency_contact: str = ""

        # SOS system
        self.alert_system = AlertSystem(db=self.db)
        self.sos_monitor = SOSMonitor(on_trigger=self._on_sos_trigger)

        # Selection confirmer (shared by phrase board and pain scale)
        self.confirmer = SelectionConfirmer(
            dwell_time_ms=1200,
            confirm_timeout_s=3.0,
        )

        # UI components (initialized after Tk root)
        self.root: Optional[tk.Tk] = None
        self.phrase_board: Optional[PhraseBoardUI] = None
        self.pain_scale: Optional[PainScaleUI] = None
        self.reorderer: Optional[TileReorderer] = None

    # ── Lifecycle ─────────────────────────────────────────────────────

    def run(self):
        """Main entry point — start the full application."""
        logger.info("=" * 60)
        logger.info("  GazeAssist — Assistive Communication System")
        logger.info("=" * 60)

        # 1. Start shared perception
        if not self.perception.start():
            logger.error("Failed to start camera/perception. Exiting.")
            sys.exit(1)
        logger.info("✓ Shared perception started")

        # 2. Start SOS monitor
        # SOS is now triggered by selecting an EMERGENCY tile, not by a
        # 3-blink pattern. sos_monitor is left unstarted/unused.

        # 3. Check for existing user
        users = self.db.get_all_users()

        # 4. Create Tk root
        self.root = tk.Tk()
        self.root.title("GazeAssist")
        # Maximize to fill the screen (keeps title bar / close button,
        # unlike true borderless fullscreen) — calibration and the phrase
        # board both size themselves to whatever this window's actual
        # size turns out to be.
        try:
            self.root.state("zoomed")  # Windows/most Linux window managers
        except tk.TclError:
            self.root.attributes("-zoomed", True)  # some Linux WMs use this instead
        self.root.configure(bg="#0F172A")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Hide the main window until it actually has content (setup wizard /
        # calibration run in their own window first) — otherwise an empty
        # 1024x700 window sits on screen alongside them.
        # self.root.withdraw()

        if users:
            # Existing user — skip setup
            user = users[0]
            self._setup_session(user["id"], user["language"],
                                user.get("emergency_contact", ""),
                                user.get("name", "Patient"))
        else:
            # First-run wizard
            self._run_setup_wizard()

        # Start Tkinter mainloop (blocks until window closes)
        self.root.mainloop()

    def _run_setup_wizard(self):
        """Launch the first-run setup wizard."""
        wizard = SetupWizard(
            on_complete=self._on_setup_complete,
            parent=self.root,
        )
        wizard.start()

    def _on_setup_complete(self, name: str, language: str, emergency_contact: str):
        """Called when setup wizard finishes."""
        # Create user
        user_id = self.db.create_user(name, language, emergency_contact)
        logger.info("User created: id=%d name=%s language=%s", user_id, name, language)

        self._setup_session(user_id, language, emergency_contact, name)

    def _setup_session(self, user_id: int, language: str,
                       emergency_contact: str, name: str):
        """Initialize session for a user."""
        self.user_id = user_id
        self.language = language
        self.emergency_contact = emergency_contact

        # Create session
        self.session_id = self.db.create_session(user_id)
        logger.info("Session started: id=%d user=%s", self.session_id, name)

        # Configure alert system
        self.alert_system = AlertSystem(
            db=self.db, session_id=self.session_id,
            emergency_contact=emergency_contact,
        )

        # Start Flask dashboard (daemon thread)
        start_dashboard(self.db, self.session_id)
        logger.info("✓ Dashboard at http://localhost:5000")

        # Set up tile reorderer
        self.reorderer = TileReorderer(self.db, user_id)

        # Calibration only lives in memory for this process (there's no
        # save/load of the fitted gaze model to disk yet), so every fresh
        # launch needs to calibrate once before gaze tracking will work.
        if self.gaze_predictor.is_calibrated:
            self._enter_navigate_mode()
        else:
            self._run_calibration()

    def _run_calibration(self):
        """Run the 6-point gaze calibration."""
        def on_calibration_done(features, quad_labels, zone_labels):
            self.gaze_predictor.calibrate(features, quad_labels, zone_labels)
            logger.info("✓ Gaze calibration complete — %d samples", len(features))

            # Save calibration data
            user_dir = os.path.join(DATA_DIR, "users", str(self.user_id))
            os.makedirs(user_dir, exist_ok=True)

            # Proceed to blink enrollment, then navigate
            self.state_machine.transition_to(AppMode.NAVIGATE)
            self._run_blink_enrollment()

        calib = CalibrationScreen(
            perception=self.perception,
            on_complete=on_calibration_done,
            parent=self.root,
        )
        calib.start()

    def _run_blink_enrollment(self):
        """Personalise the blink (EAR) threshold — FAST.

        Old behaviour ran the full 3-phase / 30-blink BlinkEnrollment
        window (with "Begin" buttons a motor-impaired user can't press)
        on EVERY launch. Now:
          * returning user  -> load saved threshold instantly (0 s)
          * first launch    -> ~1.5 s silent "keep your eyes open" baseline,
                               no button presses, saved for next time
        """
        user_dir = os.path.join(DATA_DIR, "users", str(self.user_id))
        loader = BlinkEnrollment(self.perception, compute_both_ears, lambda _: None)

        saved = loader.load_enrollment(user_dir)
        if saved:
            self._apply_blink_threshold(saved.ear_threshold)
            logger.info("✓ Loaded saved blink threshold %.3f", saved.ear_threshold)
            self._enter_navigate_mode()
            return

        self._quick_blink_baseline(loader, user_dir)

    def _quick_blink_baseline(self, loader, user_dir):
        """Measure open-eye EAR for ~1.5 s and derive the blink threshold."""
        overlay = tk.Frame(self.root, bg="#0F172A")
        overlay.pack(fill="both", expand=True)
        tk.Label(
            overlay, text="Getting ready…", font=("Segoe UI", 28, "bold"),
            fg="#F1F5F9", bg="#0F172A",
        ).pack(expand=True, pady=(0, 8), anchor="s")
        tk.Label(
            overlay, text="Keep your eyes open and look at the screen",
            font=("Segoe UI", 16), fg="#94A3B8", bg="#0F172A",
        ).pack(expand=True, anchor="n")

        samples: list[float] = []

        def sample(frames_left: int = 45):
            frame = self.perception.get_current_frame()
            if frame.face_detected and frame.landmarks:
                try:
                    _, _, avg = compute_both_ears(frame.landmarks)
                    samples.append(avg)
                except Exception:
                    pass
            if frames_left > 0:
                self.root.after(33, sample, frames_left - 1)
                return

            overlay.destroy()
            if len(samples) >= 10:
                # drop the lowest 20% in case a natural blink slipped in
                vals = sorted(samples)[len(samples) // 5:]
                avg_open = sum(vals) / len(vals)
                threshold = avg_open * 0.7
                self._apply_blink_threshold(threshold)
                from blink.enrollment import EnrollmentData
                data = EnrollmentData(ear_threshold=self.blink_classifier.rule_based.ear_threshold,
                                      avg_open_ear=avg_open)
                try:
                    loader.save_enrollment(data, user_dir)
                except Exception as e:
                    logger.error("Could not save blink baseline: %s", e)
                logger.info("✓ Blink baseline: open=%.3f threshold=%.3f",
                            avg_open, self.blink_classifier.rule_based.ear_threshold)
            else:
                logger.warning("Blink baseline: face not seen, keeping default threshold")
            self._enter_navigate_mode()

        self.root.after(300, sample)

    def _apply_blink_threshold(self, threshold: float):
        """Clamp to a safe range so one bad reading can't break blinking."""
        threshold = max(0.15, min(0.26, float(threshold)))
        self.blink_classifier.rule_based.ear_threshold = threshold

    def _enter_navigate_mode(self):
        """Set up the Navigate mode UI."""
        # Create phrase board
        self.confirmer = SelectionConfirmer(dwell_time_ms=1200, confirm_timeout_s=3.0)

        self.phrase_board = PhraseBoardUI(
            root=self.root,
            confirmer=self.confirmer,
            language=self.language,
            on_phrase_confirmed=self._on_phrase_confirmed,
            on_pain_scale=self._enter_pain_mode,
            reorderer=self.reorderer,
        )

        # Create pain scale (hidden initially)
        self.pain_confirmer = SelectionConfirmer(dwell_time_ms=1200, confirm_timeout_s=3.0)
        self.pain_scale = PainScaleUI(
            root=self.root,
            confirmer=self.pain_confirmer,
            language=self.language,
            on_pain_confirmed=self._on_pain_confirmed,
            on_back=self._exit_pain_mode,
        )
        self.pain_scale.hide()

        logger.info("✓ Navigate mode active — phrase board ready")

        # Now that the phrase board exists, reveal the main window
        # if self.root:
        #     self.root.deiconify()
        #     self.root.update_idletasks()
        #     self.root.lift()
        #     self.root.focus_force()

        # Start the perception → UI update loop
        self._update_loop()

    # ── Main update loop ──────────────────────────────────────────────

    def _update_loop(self):
        """Called every ~33ms to process perception data and update UI."""
        try:
            if not self.root:
                return

            if not hasattr(self, "_tick_count"):
                self._tick_count = 0
            self._tick_count += 1
            if self._tick_count % 30 == 0:  # once per second at 30Hz
                logger.debug("UPDATE LOOP TICK #%d", self._tick_count)

            frame = self.perception.get_current_frame()
            now = time.time()

            if frame.face_detected and frame.landmarks:
                # 1. Blink detection
                avg_ear = None
                try:
                    _, _, avg_ear = compute_both_ears(frame.landmarks)
                    blink = self.blink_classifier.update(avg_ear, now)

                    # self.sos_monitor.on_blink(blink)

                    mode = self.state_machine.current_mode
                    if mode == AppMode.NAVIGATE:
                        self.confirmer.on_blink(blink, now)
                    elif mode == AppMode.PAIN:
                        self.pain_confirmer.on_blink(blink, now)

                except Exception:
                    logger.exception("Blink processing error")

                # 2. Gaze prediction — skip while eyes are closing/closed
                # (mid-blink), since iris landmarks are unreliable then and
                # would corrupt the locked gaze zone right as the user
                # tries to confirm a selection.
                eyes_open = avg_ear is None or avg_ear >= self.blink_classifier.rule_based.ear_threshold
                try:
                    if eyes_open and self.gaze_predictor.is_calibrated:
                        features = extract_gaze_features(
                            frame.landmarks, frame.frame_width, frame.frame_height
                        )
                        # predict_zone() now includes EMA temporal smoothing
                        raw_zone = self.gaze_predictor.predict_zone(features)
                        self._zone_history.append(raw_zone)
                        counts = Counter(self._zone_history)
                        candidate, candidate_count = counts.most_common(1)[0]
                        # Lighter majority threshold (60%) since the model's
                        # EMA smoother already filters single-frame glitches.
                        threshold = max(1, int(len(self._zone_history) * 0.6))
                        if candidate != self._locked_zone and candidate_count >= threshold:
                            self._locked_zone = candidate
                        zone = self._locked_zone
                        logger.debug("PREDICTED ZONE: raw=%s locked=%s", raw_zone, zone)

                        mode = self.state_machine.current_mode
                        if mode == AppMode.NAVIGATE and self.phrase_board:
                            self.phrase_board.update_gaze_zone(zone, now)
                        elif mode == AppMode.PAIN and self.pain_scale:
                            self.pain_scale.update_gaze_zone(zone, now)
                    elif not eyes_open:
                        logger.debug("Skipping gaze update — eyes closing (avg_ear below threshold)")
                    else:
                        logger.debug("Gaze predictor not calibrated yet")

                except Exception:
                    logger.exception("Gaze processing error")
            else:
                logger.debug("No face detected this frame")

            if self.phrase_board:
                self.phrase_board.update_fps(self.perception.actual_fps)

        except Exception:
            logger.exception("FATAL error in _update_loop")

        finally:
            if self.root:
                self.root.after(FRAME_UPDATE_MS, self._update_loop)

    # ── Callbacks ─────────────────────────────────────────────────────

    def _on_phrase_confirmed(self, tile: Tile):
        """Handle confirmed phrase selection — speak + log."""
        text = get_tile_text(tile, self.language)

        # Speak via TTS (in background to not block UI)
        import threading
        threading.Thread(
            target=lambda: self.tts.speak(text, self.language),
            daemon=True,
        ).start()

        # Log to database
        if self.session_id:
            self.db.log_communication(self.session_id, tile.text, tile.category.name)

        logger.info("Spoke: '%s' (%s)", text, tile.text)

        # Selecting an EMERGENCY tile fires the same alert system that
        # used to be triggered by 3 long blinks.
        if getattr(tile, "is_emergency", False):
            self._on_sos_trigger(reason=text)

    def _on_pain_confirmed(self, level: int):
        """Handle confirmed pain level — speak + log."""
        pain_texts = {
            "hi": f"दर्द स्तर {level}",
            "ml": f"വേദന ലെവൽ {level}",
            "ta": f"வலி நிலை {level}",
            "te": f"నొప్పి స్థాయి {level}",
        }
        text = pain_texts.get(self.language, f"Pain level {level}")

        import threading
        threading.Thread(
            target=lambda: self.tts.speak(text, self.language),
            daemon=True,
        ).start()

        if self.session_id:
            self.db.log_pain(self.session_id, level)

        logger.info("Pain level: %d", level)

    def _enter_pain_mode(self):
        """Switch to pain scale mode."""
        self.state_machine.transition_to(AppMode.PAIN)
        if self.phrase_board:
            self.phrase_board.hide()
        if self.pain_scale:
            self.pain_scale.show()
        logger.info("Entered pain scale mode")

    def _exit_pain_mode(self):
        """Return to navigate mode from pain scale."""
        self.state_machine.transition_to(AppMode.NAVIGATE)
        if self.pain_scale:
            self.pain_scale.hide()
        if self.phrase_board:
            self.phrase_board.show()
        logger.info("Returned to navigate mode")

    def _on_sos_trigger(self, reason: str = "Emergency signal"):
        """Handle SOS trigger — fire all alerts."""
        logger.critical("🚨 SOS TRIGGERED! Reason: %s", reason)

        # Set dashboard SOS flag
        set_sos_flag()

        # Fire all alert channels in the background — fire_all_alerts()
        # join()s its worker threads (network calls to Meta/Fast2SMS), and
        # running that on the Tk thread froze the UI for 1-2 s per SOS.
        import threading
        threading.Thread(
            target=self.alert_system.fire_all_alerts,
            kwargs={"reason": reason}, daemon=True,
        ).start()

    def _on_close(self):
        """Clean shutdown."""
        logger.info("Shutting down...")

        # End session
        if self.session_id:
            self.db.end_session(self.session_id)

        # Stop components
        self.sos_monitor.stop()
        self.perception.stop()

        # Close window
        if self.root:
            self.root.destroy()
            self.root = None

        logger.info("GazeAssist stopped.")


# ── Entry Point ───────────────────────────────────────────────────────

def main():
    app = GazeAssistApp()
    app.run()


if __name__ == "__main__":
    main()