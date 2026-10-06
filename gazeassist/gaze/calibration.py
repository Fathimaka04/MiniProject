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

import random

from gaze.features import extract_gaze_features
from phrase_board.ui import HEADER_H, STATUS_H, GRID_PAD_X, GRID_PAD_TOP

logger = logging.getLogger(__name__)

GRID_COLS = 3
GRID_ROWS = 2

SAMPLES_PER_POINT = 30       # ~1.0 s at 30 fps
CALIBRATION_ROUNDS = 1       # set to 2 for best accuracy (+~13 s): every point shown twice, second round shuffled
MIN_KEEP = 10                # min clean samples per point after filtering
SETTLE_TIME_MS = 1000        # time to fixate before recording (was 1500)
POINT_DISPLAY_MS = 2300      # settle + record + gap, used for the time estimate
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
        self._point_zones: list[int] = []
        self._after_ids: list[str] = []

    # ── public ────────────────────────────────────────────────────────

    def start(self):
        """Build the calibration frame inside the parent window and begin."""
        self._frame = tk.Frame(self._parent, bg="#0F172A")
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
            bg="#0F172A",
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
        """Put each dot on the CENTRE of the tile it trains.

        The old grid used 12% screen padding, so the top dots sat near the
        header and the bottom dots near the status bar — far from where the
        tiles actually are — and it also shifted dots by head pose. The
        model then learned the wrong eye positions for each tile, which is
        why the rows got mixed up. Now the dots use the phrase board's own
        layout constants, so calibration == what the user will look at.
        """
        w, h = self._canvas_w, self._canvas_h
        gx0, gx1 = GRID_PAD_X, w - GRID_PAD_X
        gy0, gy1 = HEADER_H + GRID_PAD_TOP, h - STATUS_H
        cols = [int(gx0 + (gx1 - gx0) * (c + 0.5) / GRID_COLS) for c in range(GRID_COLS)]
        rows = [int(gy0 + (gy1 - gy0) * (r + 0.5) / GRID_ROWS) for r in range(GRID_ROWS)]
        base = [(c, r) for r in rows for c in cols]          # zone = index

        self._points, self._point_zones = [], []
        for rnd in range(CALIBRATION_ROUNDS):
            order = list(range(len(base)))
            if rnd > 0:
                random.shuffle(order)   # different approach direction each round
            for z in order:
                self._points.append(base[z])
                self._point_zones.append(z)

    # ── theme ─────────────────────────────────────────────────────────

    BG = "#0F172A"
    TEXT = "#F1F5F9"
    MUTED = "#94A3B8"
    TRACK = "#1E293B"
    ACCENT = "#38BDF8"
    SUCCESS = "#34D399"
    FONT = "Segoe UI"

    def _draw_progress(self, done_fraction: float):
        """Overall progress bar pinned to the bottom of the screen."""
        w, h = self._canvas_w, self._canvas_h
        bar_w = min(520, int(w * 0.5))
        x0 = (w - bar_w) // 2
        y = h - 48
        self._canvas.create_rectangle(x0, y, x0 + bar_w, y + 8,
                                      fill=self.TRACK, outline="")
        self._canvas.create_rectangle(x0, y, x0 + int(bar_w * done_fraction), y + 8,
                                      fill=self.ACCENT, outline="")
        self._canvas.create_text(
            w // 2, y - 20,
            text=f"Point {min(self._current_point + 1, len(self._points))} of {len(self._points)}",
            fill=self.MUTED, font=(self.FONT, 13),
        )

    def _draw_target(self, x, y, colour, ring_extent: float = 0.0):
        """Target dot with a soft halo and an optional progress ring."""
        r = DOT_RADIUS
        self._canvas.create_oval(x - r * 2.2, y - r * 2.2, x + r * 2.2, y + r * 2.2,
                                 outline=self.TRACK, width=6)
        if ring_extent > 0:
            self._canvas.create_arc(x - r * 2.2, y - r * 2.2, x + r * 2.2, y + r * 2.2,
                                    start=90, extent=-360 * ring_extent,
                                    style="arc", outline=colour, width=6)
        self._canvas.create_oval(x - r, y - r, x + r, y + r, fill=colour, outline="")
        self._canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=self.BG, outline="")

    # ── sequence ──────────────────────────────────────────────────────

    def _show_instruction(self):
        self._canvas.configure(bg=self.BG)
        self._canvas.delete("all")
        cx, cy = self._canvas_w // 2, self._canvas_h // 2
        self._canvas.create_text(cx, cy - 60, text="Eye Calibration",
                                 fill=self.TEXT, font=(self.FONT, 34, "bold"))
        self._canvas.create_text(
            cx, cy + 5,
            text="Keep your head still and follow the dot\nwith your EYES only. Try not to blink.",
            fill=self.MUTED, font=(self.FONT, 18), justify="center",
        )
        n_points = len(self._points)
        est = 2 + n_points * POINT_DISPLAY_MS // 1000
        self._canvas.create_text(cx, cy + 75, text=f"{n_points} points  ·  about {est} seconds",
                                 fill=self.ACCENT, font=(self.FONT, 14))
        self._after(2000, self._next_point)

    def _next_point(self):
        if self._current_point >= len(self._points):
            self._finish()
            return
        x, y = self._points[self._current_point]
        self._canvas.delete("all")
        self._draw_target(x, y, self.MUTED)
        self._draw_progress(self._current_point / len(self._points))
        self._point_samples = []
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
        self._draw_target(x, y, self.ACCENT, ring_extent=frac)
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
                self._point_samples.append(feats)
            except Exception as e:
                logger.debug("Feature extraction error: %s", e)

        self._redraw_recording()

        if len(self._point_samples) >= SAMPLES_PER_POINT:
            self._collecting = False
            self._save_point_data()
            x, y = self._points[self._current_point]
            self._canvas.delete("all")
            self._draw_target(x, y, self.SUCCESS, ring_extent=1.0)
            self._current_point += 1
            self._draw_progress(self._current_point / len(self._points))
            self._after(250, self._next_point)
        else:
            self._after(33, self._collect_sample)  # ~30 Hz

    def _save_point_data(self):
        """Clean this point's samples, then store them with their zone label.

        1. Drop blink / half-closed frames (EAR well below this point's
           median) — iris landmarks are garbage when the lid covers them.
        2. Drop outliers (glance away, landmark glitch) using the median
           absolute deviation of the both-eye gaze features.
        """
        if not self._point_samples:
            return

        idx = self._current_point
        zone = self._point_zones[idx]
        S = np.array(self._point_samples)
        n_raw = len(S)

        ear = S[:, 11:13].mean(axis=1)
        clean = S[ear >= 0.8 * np.median(ear)]

        if len(clean) >= MIN_KEEP:
            key = clean[:, 19:21]                       # h_mean, v_mean
            med = np.median(key, axis=0)
            mad = np.median(np.abs(key - med), axis=0) * 1.4826 + 1e-6
            clean = clean[np.all(np.abs(key - med) <= 3.0 * mad, axis=1)]

        if len(clean) < MIN_KEEP:
            logger.warning("Point %d: only %d clean samples, keeping all %d",
                           idx, len(clean), n_raw)
            clean = S

        for feat in clean:
            self._features.append(feat)
            self._quad_labels.append(zone)
            self._zone_labels.append(zone)

        logger.info("Point %d: %d/%d samples kept -> zone=%d",
                    idx, len(clean), n_raw, zone)

    def _finish(self):
        """Calibration complete — pass data to callback."""
        self._canvas.delete("all")
        self._canvas.create_text(
            self._canvas_w // 2, self._canvas_h // 2,
            text="✓  Calibration complete",
            fill=self.SUCCESS, font=(self.FONT, 30, "bold"),
        )

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