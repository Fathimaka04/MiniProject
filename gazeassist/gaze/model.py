"""
GazeAssist - Gaze predictor (2-D Ridge REGRESSION onto the tile grid).

Why regression instead of a 6-class classifier
───────────────────────────────────────────────
The old RidgeClassifier treated the 6 tiles as unrelated labels. But the
tiles sit on a grid: "between tile 0 and tile 1" is a real, meaningful
place. Regressing a continuous (column, row) gaze point and then picking
the nearest tile centre:
  * uses the grid geometry (one horizontal + one vertical model instead of
    six one-vs-rest boundaries) -> far fewer ways to overfit on ~180 samples
  * gives a continuous point we can smooth (One-Euro filter) and apply
    boundary hysteresis to (see gaze/smoothing.py) -> no zone flicker
  * lets us measure calibration quality in "tile widths" of error

Public API unchanged: calibrate(features, quad_labels, zone_labels),
predict_zone(features), is_calibrated. New: predict_point(features).
"""

import logging
from typing import Optional

import numpy as np

from gaze.features import weight_gaze_features, N_FEATURES

logger = logging.getLogger(__name__)

try:
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    SKLEARN_OK = True
except ImportError:
    SKLEARN_OK = False
    logger.error("scikit-learn is not installed — run `pip install scikit-learn`.")

GRID_COLS = 3
GRID_ROWS = 2
N_ZONES = GRID_COLS * GRID_ROWS

# zone id -> (col, row) centre, row-major, same order as the tile grid
ZONE_CENTERS = np.array(
    [[c, r] for r in range(GRID_ROWS) for c in range(GRID_COLS)], dtype=np.float64
)


def point_to_zone(point) -> int:
    """Nearest tile centre for a continuous (col, row) gaze point."""
    d = np.sum((ZONE_CENTERS - np.asarray(point, dtype=np.float64)) ** 2, axis=1)
    return int(np.argmin(d))


class GazePredictor:
    """Ridge regression: 21 eye features -> (col, row) on the 3x2 grid."""

    def __init__(self, alpha: float = 1.0):
        self._alpha = alpha
        self._scaler = StandardScaler() if SKLEARN_OK else None
        self._reg = Ridge(alpha=alpha) if SKLEARN_OK else None
        self._calibrated = False
        self._gain = np.ones(2)
        self._offset = np.zeros(2)
        self.last_accuracy: Optional[float] = None

    def _prep(self, features: np.ndarray, fit: bool = False) -> np.ndarray:
        F = np.atleast_2d(np.asarray(features, dtype=np.float64))
        if F.shape[1] != N_FEATURES:
            raise ValueError(f"expected {N_FEATURES} features, got {F.shape[1]}")
        X = self._scaler.fit_transform(F) if fit else self._scaler.transform(F)
        # Weights AFTER scaling so they actually change the model.
        return weight_gaze_features(X)

    def calibrate(self, features: np.ndarray, quad_labels: np.ndarray,
                  zone_labels: Optional[np.ndarray] = None) -> Optional[float]:
        if not SKLEARN_OK:
            logger.error("scikit-learn not available — cannot calibrate gaze")
            return None

        labels = np.asarray(zone_labels if zone_labels is not None else quad_labels,
                            dtype=int)
        targets = ZONE_CENTERS[labels]
        X = self._prep(features, fit=True)
        self._reg.fit(X, targets)

        # Ridge shrinks predictions toward the screen centre, which puts
        # corner/edge tiles close to tile borders -> flicker. Fit a simple
        # per-axis linear correction so calibration predictions land back
        # on the tile centres.
        raw = self._reg.predict(X)
        self._gain = np.ones(2)
        self._offset = np.zeros(2)
        for k in range(2):
            if np.std(raw[:, k]) > 1e-6:
                g, o = np.polyfit(raw[:, k], targets[:, k], 1)
                self._gain[k], self._offset[k] = g, o
        self._calibrated = True

        # ── calibration quality report ────────────────────────────────
        pred = raw * self._gain + self._offset
        pred_zones = np.array([point_to_zone(p) for p in pred])
        acc = float(np.mean(pred_zones == labels))
        err = np.linalg.norm(pred - targets, axis=1)
        self.last_accuracy = acc
        logger.info("Gaze model calibrated on %d samples — fit accuracy %.0f%%, "
                    "mean error %.2f tile", len(labels), acc * 100, float(err.mean()))
        for z in range(N_ZONES):
            m = labels == z
            if m.any():
                z_acc = float(np.mean(pred_zones[m] == z))
                level = logging.WARNING if z_acc < 0.8 else logging.INFO
                logger.log(level, "  zone %d: %.0f%% (mean error %.2f tile)",
                           z, z_acc * 100, float(err[m].mean()))
        return acc

    def predict_point(self, features: np.ndarray) -> Optional[np.ndarray]:
        """Continuous gaze point in grid units: x in [0,2], y in [0,1]."""
        if not self._calibrated:
            return None
        return self._reg.predict(self._prep(features))[0] * self._gain + self._offset

    def predict_zone(self, features: np.ndarray) -> int:
        p = self.predict_point(features)
        return -1 if p is None else point_to_zone(p)

    # kept for backwards compatibility
    def predict_quadrant(self, features: np.ndarray) -> int:
        return self.predict_zone(features)

    @property
    def is_calibrated(self) -> bool:
        return self._calibrated