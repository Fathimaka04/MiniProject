"""
GazeAssist - Gaze smoothing + zone hysteresis.

Replaces the old "70% of last 10 frames" majority vote with:

1. One-Euro filter on the continuous (col, row) gaze point.
   Heavy smoothing while the eyes are still (kills MediaPipe iris jitter),
   light smoothing during a real eye movement (so it doesn't feel laggy).
   Standard filter for gaze/pointer input (Casiez et al., CHI 2012).

2. Spatial hysteresis: the locked zone only changes once the smoothed
   point is clearly past the edge of the current tile (by MARGIN tile
   widths).

3. Temporal hysteresis: the new tile must stay the candidate for HOLD_S
   seconds before it is accepted. A gaze point wandering around a border
   no longer flips between two tiles and restarts the dwell timer.
"""

import math
from typing import Optional

import numpy as np

GRID_COLS = 3
GRID_ROWS = 2


class _OneEuro:
    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.8, d_cutoff: float = 1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x: Optional[np.ndarray] = None
        self._dx: Optional[np.ndarray] = None
        self._t: Optional[float] = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self):
        self._x = self._dx = self._t = None

    def __call__(self, x: np.ndarray, t: float) -> np.ndarray:
        if self._x is None:
            self._x, self._dx, self._t = x.copy(), np.zeros_like(x), t
            return x
        dt = max(t - self._t, 1e-3)
        self._t = t
        dx = (x - self._x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self._dx = a_d * dx + (1 - a_d) * self._dx
        cutoff = self.min_cutoff + self.beta * np.abs(self._dx)
        a = np.array([self._alpha(c, dt) for c in np.atleast_1d(cutoff)])
        self._x = a * x + (1 - a) * self._x
        return self._x


class GazeSmoother:
    # Tile widths the smoothed point must travel PAST the current tile's
    # edge before we consider switching (spatial hysteresis).
    MARGIN = 0.30
    # ...and the new tile must stay the candidate for this long (temporal
    # hysteresis) — short wobbles across a border are ignored.
    HOLD_S = 0.25

    def __init__(self, min_cutoff: float = 0.5, beta: float = 0.3):
        # Lower min_cutoff = smoother when the eyes are still.
        # Lower beta = less "chasing" of landmark noise.
        self._filter = _OneEuro(min_cutoff=min_cutoff, beta=beta)
        self._zone = -1
        self._cand = -1
        self._cand_since = 0.0
        self.point: Optional[np.ndarray] = None

    def reset(self):
        self._filter.reset()
        self._zone = -1
        self._cand = -1
        self.point = None

    def update(self, raw_point, t: float) -> int:
        # clamp wild frames (partial blink, glare) so one bad frame can't
        # yank the filtered point across the screen
        p = np.clip(np.asarray(raw_point, dtype=np.float64),
                    [-0.5, -0.5], [GRID_COLS - 0.5, GRID_ROWS - 0.5])
        x, y = self._filter(p, t)
        self.point = np.array([x, y])

        col = int(np.clip(round(x), 0, GRID_COLS - 1))
        row = int(np.clip(round(y), 0, GRID_ROWS - 1))
        candidate = row * GRID_COLS + col

        if self._zone < 0:
            self._zone = candidate
            return self._zone

        if candidate == self._zone:
            self._cand = -1
            return self._zone

        cur_col, cur_row = self._zone % GRID_COLS, self._zone // GRID_COLS
        clearly_out = (abs(x - cur_col) > 0.5 + self.MARGIN
                       or abs(y - cur_row) > 0.5 + self.MARGIN)
        if not clearly_out:
            self._cand = -1
            return self._zone

        if candidate != self._cand:
            self._cand, self._cand_since = candidate, t
        elif t - self._cand_since >= self.HOLD_S:
            self._zone = candidate
            self._cand = -1
        return self._zone