"""
GazeAssist - Per-user blink enrollment flow.

Guides the user through ~2 minutes of prompted blinks to train the
blink classifier on their personal EAR patterns.
"""

import time
import os
import json
import logging
import threading
import tkinter as tk
from typing import Optional, Callable
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class EnrollmentData:
    """Collected blink enrollment data for one user."""
    natural_blinks: list = field(default_factory=list)       # EAR windows during natural blinks
    short_deliberate: list = field(default_factory=list)      # EAR windows during short deliberate
    long_deliberate: list = field(default_factory=list)       # EAR windows during long deliberate
    ear_threshold: float = 0.21                               # Calibrated EAR threshold
    avg_open_ear: float = 0.3                                 # Average EAR when eyes open
    user_id: Optional[int] = None


class BlinkEnrollment:
    """
    Guides the user through blink enrollment to calibrate the classifier.

    Sequence: 10 natural blinks, 10 short deliberate, 10 long deliberate.
    Records EAR time series with labels. Computes personalized thresholds.
    """

    PROMPTS = [
        ("Natural Blinks", "Blink normally 10 times at your natural pace", 10, "natural"),
        ("Short Deliberate Blinks", "Blink firmly and quickly 10 times\n(like pressing a button)", 10, "short"),
        ("Long Deliberate Blinks", "Close your eyes for 1 second, then open\nRepeat 10 times", 10, "long"),
    ]

    def __init__(self, perception, ear_computer, on_complete: Callable[[EnrollmentData], None]):
        """
        Args:
            perception: SharedPerception instance for reading landmarks.
            ear_computer: Function (landmarks) -> (left_ear, right_ear, avg_ear).
            on_complete: Callback with collected EnrollmentData.
        """
        self._perception = perception
        self._ear_computer = ear_computer
        self._on_complete = on_complete
        self._data = EnrollmentData()
        self._window: Optional[tk.Toplevel] = None
        self._running = False
        self._current_phase = 0
        self._blink_count = 0
        self._ear_buffer: list[tuple[float, float]] = []  # (timestamp, avg_ear)
        self._in_blink = False
        self._blink_start_time = 0.0
        self._collection_active = False

    def start(self, parent: Optional[tk.Tk] = None):
        """Start the enrollment UI."""
        self._running = True
        self._current_phase = 0

        if parent:
            self._window = tk.Toplevel(parent)
        else:
            self._window = tk.Tk()

        self._window.title("GazeAssist - Blink Enrollment")
        self._window.geometry("700x500")
        self._window.configure(bg="#1a1a2e")
        self._window.resizable(False, False)

        # Title label
        self._title_label = tk.Label(
            self._window, text="Blink Enrollment",
            font=("Helvetica", 24, "bold"), fg="#e0e0e0", bg="#1a1a2e"
        )
        self._title_label.pack(pady=20)

        # Instruction label
        self._instruction_label = tk.Label(
            self._window, text="",
            font=("Helvetica", 16), fg="#b0b0b0", bg="#1a1a2e",
            wraplength=600, justify="center"
        )
        self._instruction_label.pack(pady=10)

        # Progress label
        self._progress_label = tk.Label(
            self._window, text="",
            font=("Helvetica", 18, "bold"), fg="#4CAF50", bg="#1a1a2e"
        )
        self._progress_label.pack(pady=10)

        # EAR indicator canvas
        self._canvas = tk.Canvas(
            self._window, width=600, height=80, bg="#0f0f23", highlightthickness=0
        )
        self._canvas.pack(pady=10)

        # Status label
        self._status_label = tk.Label(
            self._window, text="Preparing...",
            font=("Helvetica", 14), fg="#808080", bg="#1a1a2e"
        )
        self._status_label.pack(pady=10)

        # Start button
        self._start_btn = tk.Button(
            self._window, text="Begin Enrollment", font=("Helvetica", 14, "bold"),
            bg="#4CAF50", fg="white", command=self._begin_phase,
            padx=20, pady=10
        )
        self._start_btn.pack(pady=20)

        self._show_phase_prompt()

    def _show_phase_prompt(self):
        """Show instructions for the current phase."""
        if self._current_phase >= len(self.PROMPTS):
            self._finish_enrollment()
            return

        title, instruction, count, _ = self.PROMPTS[self._current_phase]
        self._title_label.config(text=f"Phase {self._current_phase + 1}/3: {title}")
        self._instruction_label.config(text=instruction)
        self._progress_label.config(text=f"0 / {count} blinks detected")
        self._status_label.config(text="Press 'Begin' when ready")
        self._start_btn.config(state="normal", text="Begin")
        self._blink_count = 0

    def _begin_phase(self):
        """Start collecting blinks for the current phase."""
        self._start_btn.config(state="disabled")
        self._status_label.config(text="Recording... blink now!")
        self._collection_active = True
        self._ear_buffer = []
        self._in_blink = False
        self._blink_count = 0

        # Collect baseline EAR first (1 second of open eyes)
        self._status_label.config(text="Keep eyes open for 1 second...")
        self._window.after(1000, self._start_blink_detection)

    def _start_blink_detection(self):
        """Begin the blink detection loop for current phase."""
        # Calculate baseline EAR from recent frames
        baseline_ears = []
        for _ in range(10):
            frame = self._perception.get_current_frame()
            if frame.face_detected and frame.landmarks:
                try:
                    _, _, avg = self._ear_computer(frame.landmarks)
                    baseline_ears.append(avg)
                except Exception:
                    pass

        if baseline_ears:
            self._data.avg_open_ear = np.mean(baseline_ears)
            self._data.ear_threshold = self._data.avg_open_ear * 0.7

        self._status_label.config(text="Blink now!")
        self._collection_active = True
        self._monitor_blinks()

    def _monitor_blinks(self):
        """Periodic check for blinks during enrollment."""
        if not self._running or not self._collection_active:
            return

        frame = self._perception.get_current_frame()
        if frame.face_detected and frame.landmarks:
            try:
                _, _, avg_ear = self._ear_computer(frame.landmarks)
                now = time.time()
                self._ear_buffer.append((now, avg_ear))

                # Trim buffer to last 5 seconds
                cutoff = now - 5.0
                self._ear_buffer = [(t, e) for t, e in self._ear_buffer if t > cutoff]

                # Draw EAR visualization
                self._draw_ear_bar(avg_ear)

                # Detect blink transitions
                threshold = self._data.ear_threshold
                if not self._in_blink and avg_ear < threshold:
                    self._in_blink = True
                    self._blink_start_time = now
                elif self._in_blink and avg_ear >= threshold:
                    self._in_blink = False
                    blink_duration = now - self._blink_start_time

                    # Record the blink's EAR window
                    ear_window = [e for t, e in self._ear_buffer
                                  if t >= self._blink_start_time - 0.2
                                  and t <= now + 0.1]

                    _, _, target_count, phase_type = self.PROMPTS[self._current_phase]

                    if phase_type == "natural" and blink_duration < 0.4:
                        self._data.natural_blinks.append(ear_window)
                        self._blink_count += 1
                    elif phase_type == "short" and 0.1 < blink_duration < 1.0:
                        self._data.short_deliberate.append(ear_window)
                        self._blink_count += 1
                    elif phase_type == "long" and blink_duration > 0.4:
                        self._data.long_deliberate.append(ear_window)
                        self._blink_count += 1

                    self._progress_label.config(
                        text=f"{self._blink_count} / {target_count} blinks detected"
                    )

                    if self._blink_count >= target_count:
                        self._collection_active = False
                        self._current_phase += 1
                        self._window.after(1000, self._show_phase_prompt)
                        return

            except Exception as e:
                logger.error(f"Enrollment monitor error: {e}")

        # Continue monitoring at ~30Hz
        self._window.after(33, self._monitor_blinks)

    def _draw_ear_bar(self, ear_value: float):
        """Draw EAR level indicator on canvas."""
        self._canvas.delete("all")
        w, h = 600, 80
        threshold = self._data.ear_threshold

        # Background
        self._canvas.create_rectangle(0, 0, w, h, fill="#0f0f23")

        # EAR bar
        bar_width = min(ear_value / 0.5, 1.0) * (w - 40)
        color = "#4CAF50" if ear_value >= threshold else "#F44336"
        self._canvas.create_rectangle(20, 20, 20 + bar_width, 60, fill=color)

        # Threshold line
        thresh_x = 20 + (threshold / 0.5) * (w - 40)
        self._canvas.create_line(thresh_x, 10, thresh_x, 70, fill="#FFD700", width=2)
        self._canvas.create_text(thresh_x, 5, text="threshold", fill="#FFD700", font=("Helvetica", 8))

        # EAR value text
        self._canvas.create_text(w // 2, h - 10, text=f"EAR: {ear_value:.3f}",
                                 fill="#e0e0e0", font=("Helvetica", 10))

    def _finish_enrollment(self):
        """Enrollment complete — compute calibrated parameters and notify."""
        self._running = False
        self._title_label.config(text="Enrollment Complete!")
        self._instruction_label.config(text="Your blink profile has been calibrated.")
        self._status_label.config(
            text=f"Collected: {len(self._data.natural_blinks)} natural, "
                 f"{len(self._data.short_deliberate)} short, "
                 f"{len(self._data.long_deliberate)} long"
        )
        self._start_btn.config(state="normal", text="Continue", command=self._close_and_complete)

        logger.info(
            f"Enrollment complete: {len(self._data.natural_blinks)} natural, "
            f"{len(self._data.short_deliberate)} short deliberate, "
            f"{len(self._data.long_deliberate)} long deliberate blinks"
        )

    def _close_and_complete(self):
        """Close enrollment window and fire callback."""
        if self._window:
            self._window.destroy()
            self._window = None
        self._on_complete(self._data)

    def save_enrollment(self, data: EnrollmentData, save_dir: str):
        """Save enrollment data to disk for future sessions."""
        os.makedirs(save_dir, exist_ok=True)

        # Save EAR windows as numpy arrays
        np.savez(
            os.path.join(save_dir, "blink_enrollment.npz"),
            natural=np.array([w for w in data.natural_blinks], dtype=object),
            short=np.array([w for w in data.short_deliberate], dtype=object),
            long=np.array([w for w in data.long_deliberate], dtype=object),
        )

        # Save calibrated parameters
        params = {
            "ear_threshold": data.ear_threshold,
            "avg_open_ear": data.avg_open_ear,
        }
        with open(os.path.join(save_dir, "blink_params.json"), "w") as f:
            json.dump(params, f, indent=2)

        logger.info(f"Enrollment data saved to {save_dir}")

    def load_enrollment(self, save_dir: str) -> Optional[EnrollmentData]:
        """Load previously saved enrollment data."""
        params_path = os.path.join(save_dir, "blink_params.json")
        if not os.path.exists(params_path):
            return None

        try:
            with open(params_path) as f:
                params = json.load(f)

            data = EnrollmentData(
                ear_threshold=params.get("ear_threshold", 0.21),
                avg_open_ear=params.get("avg_open_ear", 0.3),
            )
            return data
        except Exception as e:
            logger.error(f"Failed to load enrollment data: {e}")
            return None
