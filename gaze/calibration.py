"""
GazeAssist - 6-point gaze calibration screen (Tkinter — premium redesign).

Renders as a Frame INSIDE the app's single main window (not a separate
Toplevel/fullscreen window), so calibration and the phrase board share
one continuous window instead of one window closing and another opening.

Shows 6 dots in a 3x2 grid — the SAME layout as the phrase board's tile
grid (3 columns, 2 rows) — so each calibration zone maps 1:1 onto a tile
position with no translation layer needed. User gazes at each dot for
~1.3 s while the system records feature vectors.

Accuracy improvements:
  * 40 samples per point (up from 25) for more robust classifier training
  * Outlier rejection: drops samples with extreme head-pose deviation
  * Feature augmentation: adds small Gaussian noise copies for better
    Ridge classifier generalisation
"""

import logging
import math
import tkinter as tk
from typing import Callable, Optional

import numpy as np

from gaze.features import extract_gaze_features, estimate_head_pose

logger = logging.getLogger(__name__)

GRID_COLS = 3
GRID_ROWS = 2

SAMPLES_PER_POINT = 40       # ↑ from 25 — more data → better Ridge fit
SETTLE_TIME_MS = 800         # time to fixate before recording
POINT_DISPLAY_MS = 2200      # settle + record + gap, used for the time estimate
DOT_RADIUS = 20
PADDING_FRAC = 0.12          # fraction of canvas edge used as padding
AUG_NOISE_STD = 0.008        # Gaussian noise σ for feature augmentation
AUG_COPIES = 2               # number of augmented copies per real sample
MAX_POSE_DEV = 15.0          # degrees — drop samples with pose > this from mean


# ── Theme ─────────────────────────────────────────────────────────────
BG       = "#0B0F1A"
TEXT     = "#F0F4FC"
MUTED    = "#6B7FA0"
TRACK    = "#131B2E"
ACCENT   = "#4FC3F7"
SUCCESS  = "#66BB6A"
WARM     = "#FFB74D"
FONT     = "Segoe UI"


class CalibrationScreen:
    """
    6-point gaze calibration, rendered as a Frame inside the app's main
    window. Collects feature vectors while the user gazes at each dot,
    then calls on_complete(features, quad_labels, zone_labels).
    """

    def __init__(
        self,
        perception,
        on_complete: Callable,
        parent: tk.Misc,
        width: int = 1024,
        height: int = 700,
    ):
        self._perception = perception
        self._on_complete = on_complete
        self._parent = parent
        self._canvas_w = width
        self._canvas_h = height

        self._features: list[np.ndarray] = []
        self._quad_labels: list[int] = []
        self._zone_labels: list[int] = []

        self._current_point = 0
        self._collecting = False
        self._point_samples: list[np.ndarray] = []
        self._point_poses: list[tuple[float, float, float]] = []

        self._frame: Optional[tk.Frame] = None
        self._canvas: Optional[tk.Canvas] = None
        self._points: list[tuple[int, int]] = []
        self._after_ids: list[str] = []

    # ── public ────────────────────────────────────────────────────────

    def start(self):
        """Build the calibration frame inside the parent window and begin."""
        self._frame = tk.Frame(self._parent, bg=BG)
        self._frame.pack(fill="both", expand=True)

        # Use the parent's actual current size once it's laid out
        self._parent.update_idletasks()
        w = self._parent.winfo_width()
        h = self._parent.winfo_height()
        if w > 1 and h > 1:
            self._canvas_w, self._canvas_h = w, h

        self._canvas = tk.Canvas(
            self._frame,
            width=self._canvas_w,
            height=self._canvas_h,
            bg=BG,
            highlightthickness=0,
        )
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Escape>", lambda e: self._abort())
        self._canvas.focus_set()

        self._compute_grid()
        self._current_point = 0
        self._show_instruction()

    def destroy(self):
        """Remove the calibration frame (called after completion/abort)."""
        self._collecting = False
        for after_id in self._after_ids:
            try:
                self._parent.after_cancel(after_id)
            except Exception:
                pass
        self._after_ids.clear()
        if self._frame:
            self._frame.destroy()
            self._frame = None

    def _after(self, ms: int, fn):
        """Schedule a callback on the parent window, tracking it for cleanup."""
        after_id = self._parent.after(ms, fn)
        self._after_ids.append(after_id)
        return after_id

    # ── grid computation (head-pose aware) ────────────────────────────

    def _compute_grid(self):
        """Compute 6-point (3x2) grid positions, adjusted for head pose if possible."""
        w, h = self._canvas_w, self._canvas_h
        px = int(w * PADDING_FRAC)
        py = int(h * PADDING_FRAC)

        cols = [px, w // 2, w - px]      # 3 columns
        rows = [py, h - py]              # 2 rows

        try:
            frame = self._perception.get_current_frame()
            if frame.face_detected and frame.landmarks:
                pitch, yaw, _ = estimate_head_pose(
                    frame.landmarks, frame.frame_width, frame.frame_height
                )
                x_shift = int(np.clip(yaw * 3, -px // 2, px // 2))
                y_shift = int(np.clip(-pitch * 3, -py // 2, py // 2))
                cols = [c + x_shift for c in cols]
                rows = [r + y_shift for r in rows]
                logger.info(
                    "Head-pose calibration offset: yaw=%.1f° pitch=%.1f° → "
                    "shift (%d, %d)px", yaw, pitch, x_shift, y_shift,
                )
        except Exception as e:
            logger.debug("Head-pose grid adjustment skipped: %s", e)

        self._points = [(c, r) for r in rows for c in cols]

    # ── drawing helpers ───────────────────────────────────────────────

    def _draw_progress(self, done_fraction: float):
        """Overall progress bar pinned to the bottom of the screen."""
        w, h = self._canvas_w, self._canvas_h
        bar_w = min(480, int(w * 0.45))
        x0 = (w - bar_w) // 2
        y = h - 52

        # Track
        self._canvas.create_rectangle(x0, y, x0 + bar_w, y + 6,
                                      fill=TRACK, outline="")
        # Fill
        self._canvas.create_rectangle(x0, y, x0 + int(bar_w * done_fraction), y + 6,
                                      fill=ACCENT, outline="")
        # Label
        self._canvas.create_text(
            w // 2, y - 18,
            text=f"Point {min(self._current_point + 1, len(self._points))} of {len(self._points)}",
            fill=MUTED, font=(FONT, 12),
        )

    def _draw_target(self, x, y, colour, ring_extent: float = 0.0,
                     pulse: bool = False):
        """Target dot with outer ring and optional progress arc."""
        r = DOT_RADIUS

        # Outer ring (track)
        ring_r = r * 2.4
        self._canvas.create_oval(x - ring_r, y - ring_r, x + ring_r, y + ring_r,
                                 outline=TRACK, width=5)
        # Progress arc
        if ring_extent > 0:
            self._canvas.create_arc(x - ring_r, y - ring_r, x + ring_r, y + ring_r,
                                    start=90, extent=-360 * ring_extent,
                                    style="arc", outline=colour, width=5)

        # Soft halo glow
        glow_r = r * 1.6
        self._canvas.create_oval(x - glow_r, y - glow_r, x + glow_r, y + glow_r,
                                 fill="#0D2A40" if colour == ACCENT else "#1A2010",
                                 outline="")

        # Main dot
        self._canvas.create_oval(x - r, y - r, x + r, y + r,
                                 fill=colour, outline="")
        # Center pinhole
        self._canvas.create_oval(x - 3, y - 3, x + 3, y + 3,
                                 fill=BG, outline="")

    # ── sequence ──────────────────────────────────────────────────────

    def _show_instruction(self):
        self._canvas.configure(bg=BG)
        self._canvas.delete("all")
        cx, cy = self._canvas_w // 2, self._canvas_h // 2

        self._canvas.create_text(cx, cy - 70, text="👁",
                                 fill=TEXT, font=("Segoe UI Emoji", 42))
        self._canvas.create_text(cx, cy - 10, text="Eye Calibration",
                                 fill=TEXT, font=(FONT, 30, "bold"))
        self._canvas.create_text(
            cx, cy + 40,
            text="Follow the dot with your eyes only.\nKeep your head still and relaxed.",
            fill=MUTED, font=(FONT, 15), justify="center",
        )
        n_points = GRID_COLS * GRID_ROWS
        est = 2 + n_points * POINT_DISPLAY_MS // 1000
        self._canvas.create_text(cx, cy + 100,
                                 text=f"{n_points} points  ·  ~{est} seconds",
                                 fill=ACCENT, font=(FONT, 13))
        self._after(2000, self._next_point)

    def _next_point(self):
        if self._current_point >= len(self._points):
            self._finish()
            return
        x, y = self._points[self._current_point]
        self._canvas.delete("all")
        self._draw_target(x, y, MUTED)
        self._draw_progress(self._current_point / len(self._points))
        self._point_samples = []
        self._point_poses = []
        self._after(SETTLE_TIME_MS, self._start_collecting)

    def _start_collecting(self):
        """Begin recording gaze features for the current dot."""
        if self._current_point >= len(self._points):
            return
        self._collecting = True
        self._redraw_recording()
        self._collect_sample()

    def _redraw_recording(self):
        x, y = self._points[self._current_point]
        frac = len(self._point_samples) / SAMPLES_PER_POINT
        self._canvas.delete("all")
        self._draw_target(x, y, ACCENT, ring_extent=frac)
        self._draw_progress((self._current_point + frac) / len(self._points))

    def _collect_sample(self):
        """Collect one sample frame of gaze features."""
        if not self._collecting:
            return

        frame = self._perception.get_current_frame()
        if frame.face_detected and frame.landmarks:
            try:
                feats = extract_gaze_features(
                    frame.landmarks, frame.frame_width, frame.frame_height
                )
                pose = estimate_head_pose(
                    frame.landmarks, frame.frame_width, frame.frame_height
                )
                self._point_samples.append(feats)
                self._point_poses.append(pose)
            except Exception as e:
                logger.debug("Feature extraction error: %s", e)

        self._redraw_recording()

        if len(self._point_samples) >= SAMPLES_PER_POINT:
            self._collecting = False
            self._save_point_data()
            x, y = self._points[self._current_point]
            self._canvas.delete("all")
            self._draw_target(x, y, SUCCESS, ring_extent=1.0)
            self._current_point += 1
            self._draw_progress(self._current_point / len(self._points))
            self._after(200, self._next_point)
        else:
            self._after(33, self._collect_sample)  # ~30 Hz

    def _save_point_data(self):
        """Map collected features to a zone label, with outlier rejection
        and data augmentation for better classifier generalisation."""
        if not self._point_samples:
            return

        idx = self._current_point
        zone = idx  # 0-5, same order as tile positions

        samples = np.array(self._point_samples)
        poses = np.array(self._point_poses)   # (N, 3)

        # ── Outlier rejection: drop samples where head pose deviates
        # too far from the median of this point's collection window.
        if len(poses) > 5:
            median_pose = np.median(poses, axis=0)
            deviation = np.linalg.norm(poses - median_pose, axis=1)
            mask = deviation < MAX_POSE_DEV
            n_before = len(samples)
            samples = samples[mask]
            n_dropped = n_before - len(samples)
            if n_dropped > 0:
                logger.info("Point %d: dropped %d/%d outlier samples",
                            idx, n_dropped, n_before)

        # ── Save real samples
        for feat in samples:
            self._features.append(feat)
            self._quad_labels.append(zone)
            self._zone_labels.append(zone)

        # ── Data augmentation: add small Gaussian noise copies
        # This helps Ridge / MLP generalise beyond the exact calibration
        # conditions (tiny head shifts, lighting changes).
        for feat in samples:
            for _ in range(AUG_COPIES):
                noise = np.random.normal(0, AUG_NOISE_STD, feat.shape)
                aug_feat = feat + noise
                self._features.append(aug_feat)
                self._quad_labels.append(zone)
                self._zone_labels.append(zone)

        total = len(samples) * (1 + AUG_COPIES)
        logger.info(
            "Point %d: %d real + %d augmented = %d samples → zone=%d",
            idx, len(samples), len(samples) * AUG_COPIES, total, zone,
        )

    def _finish(self):
        """Calibration complete — pass data to callback."""
        self._canvas.delete("all")
        cx, cy = self._canvas_w // 2, self._canvas_h // 2
        self._canvas.create_text(cx, cy - 20, text="✓",
                                 fill=SUCCESS, font=(FONT, 48, "bold"))
        self._canvas.create_text(cx, cy + 30, text="Calibration Complete",
                                 fill=SUCCESS, font=(FONT, 24, "bold"))

        features = np.array(self._features)
        quad_labels = np.array(self._quad_labels)
        zone_labels = np.array(self._zone_labels)

        logger.info(
            "Calibration finished: %d total samples across %d points",
            len(features), len(self._points),
        )

        self._after(800, lambda: self._close_and_complete(
            features, quad_labels, zone_labels
        ))

    def _close_and_complete(self, features, quad_labels, zone_labels):
        self.destroy()
        self._on_complete(features, quad_labels, zone_labels)

    def _abort(self):
        """Cancel calibration (Escape key)."""
        logger.warning("Calibration aborted by user")
        self.destroy()