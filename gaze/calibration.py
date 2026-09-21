"""
GazeAssist - 6-point gaze calibration screen (Tkinter).

Renders as a Frame INSIDE the app's single main window (not a separate
Toplevel/fullscreen window), so calibration and the phrase board share
one continuous window instead of one window closing and another opening.

Shows 6 dots in a 3x2 grid — the SAME layout as the phrase board's tile
grid (3 columns, 2 rows) — so each calibration zone maps 1:1 onto a tile
position with no translation layer needed. User gazes at each dot for
~3 s while the system records feature vectors. Supports head-pose-aware
grid adjustment for non-standard camera angles.
"""

import logging
import tkinter as tk
from typing import Callable, Optional

import numpy as np

from gaze.features import extract_gaze_features, estimate_head_pose

logger = logging.getLogger(__name__)

GRID_COLS = 3
GRID_ROWS = 2

SAMPLES_PER_POINT = 45       # ~1.5 s at 30 fps
SETTLE_TIME_MS = 1500        # time to fixate before recording
POINT_DISPLAY_MS = 3000      # total time per point (settle + record)
DOT_RADIUS = 22
PADDING_FRAC = 0.12          # fraction of canvas edge used as padding


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

        self._frame: Optional[tk.Frame] = None
        self._canvas: Optional[tk.Canvas] = None
        self._points: list[tuple[int, int]] = []
        self._after_ids: list[str] = []

    # ── public ────────────────────────────────────────────────────────

    def start(self):
        """Build the calibration frame inside the parent window and begin."""
        self._frame = tk.Frame(self._parent, bg="black")
        self._frame.pack(fill="both", expand=True)

        # Use the parent's actual current size once it's laid out, falling
        # back to the constructor defaults if it isn't mapped yet.
        self._parent.update_idletasks()
        w = self._parent.winfo_width()
        h = self._parent.winfo_height()
        if w > 1 and h > 1:
            self._canvas_w, self._canvas_h = w, h

        self._canvas = tk.Canvas(
            self._frame,
            width=self._canvas_w,
            height=self._canvas_h,
            bg="black",
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

        cols = [px, w // 2, w - px]      # 3 columns, same as the tile grid
        rows = [py, h - py]              # 2 rows, same as the tile grid

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

    # ── sequence ──────────────────────────────────────────────────────

    def _show_instruction(self):
        self._canvas.delete("all")
        self._canvas.create_text(
            self._canvas_w // 2, self._canvas_h // 2 - 40,
            text="Gaze Calibration",
            fill="white", font=("Helvetica", 32, "bold"),
        )
        self._canvas.create_text(
            self._canvas_w // 2, self._canvas_h // 2 + 20,
            text="Look at each dot as it appears.\nStay focused until it turns green.",
            fill="#aaaaaa", font=("Helvetica", 18), justify="center",
        )
        n_points = GRID_COLS * GRID_ROWS
        self._canvas.create_text(
            self._canvas_w // 2, self._canvas_h // 2 + 90,
            text=f"{n_points} points · ~{n_points * POINT_DISPLAY_MS // 1000}s total",
            fill="#666666", font=("Helvetica", 14),
        )
        self._after(3000, self._next_point)

    def _next_point(self):
        if self._current_point >= len(self._points):
            self._finish()
            return

        x, y = self._points[self._current_point]
        self._canvas.delete("all")

        self._canvas.create_oval(
            x - DOT_RADIUS, y - DOT_RADIUS,
            x + DOT_RADIUS, y + DOT_RADIUS,
            fill="#FFD700", outline="#FFD700",
        )
        self._canvas.create_text(
            self._canvas_w // 2, 30,
            text=f"Point {self._current_point + 1} / {len(self._points)}",
            fill="#555555", font=("Helvetica", 14),
        )

        self._point_samples = []
        self._after(SETTLE_TIME_MS, self._start_collecting)

    def _start_collecting(self):
        """Begin recording gaze features for the current dot."""
        if self._current_point >= len(self._points):
            return

        x, y = self._points[self._current_point]
        self._canvas.delete("all")
        self._canvas.create_oval(
            x - DOT_RADIUS, y - DOT_RADIUS,
            x + DOT_RADIUS, y + DOT_RADIUS,
            fill="#4CAF50", outline="#4CAF50",
        )
        self._canvas.create_text(
            self._canvas_w // 2, 30,
            text=f"Point {self._current_point + 1} / {len(self._points)}  ● Recording",
            fill="#4CAF50", font=("Helvetica", 14),
        )

        self._collecting = True
        self._collect_sample()

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
                self._point_samples.append(feats)
            except Exception as e:
                logger.debug("Feature extraction error: %s", e)

        if len(self._point_samples) >= SAMPLES_PER_POINT:
            self._collecting = False
            self._save_point_data()
            self._current_point += 1
            self._after(300, self._next_point)
        else:
            self._after(33, self._collect_sample)  # ~30 Hz

    def _save_point_data(self):
        """Map collected features to a zone label for the current point.

        Points are laid out in the same 3-column x 2-row, row-major order
        as the phrase board's tile grid, so the calibration point index
        IS the zone id — no quadrant/sub-zone translation needed.
        """
        if not self._point_samples:
            return

        idx = self._current_point
        zone = idx  # 0-5, same order as tile positions

        for feat in self._point_samples:
            self._features.append(feat)
            self._quad_labels.append(zone)
            self._zone_labels.append(zone)

        logger.info(
            "Point %d: %d samples → zone=%d",
            idx, len(self._point_samples), zone,
        )

    def _finish(self):
        """Calibration complete — pass data to callback."""
        self._canvas.delete("all")
        self._canvas.create_text(
            self._canvas_w // 2, self._canvas_h // 2,
            text="Calibration Complete ✓",
            fill="#4CAF50", font=("Helvetica", 28, "bold"),
        )

        features = np.array(self._features)
        quad_labels = np.array(self._quad_labels)
        zone_labels = np.array(self._zone_labels)

        logger.info(
            "Calibration finished: %d total samples across %d points",
            len(features), len(self._points),
        )

        self._after(1500, lambda: self._close_and_complete(
            features, quad_labels, zone_labels
        ))

    def _close_and_complete(self, features, quad_labels, zone_labels):
        self.destroy()
        self._on_complete(features, quad_labels, zone_labels)

    def _abort(self):
        """Cancel calibration (Escape key)."""
        logger.warning("Calibration aborted by user")
        self.destroy()