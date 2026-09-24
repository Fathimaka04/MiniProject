"""
GazeAssist - Gaze MLP for zone prediction with Ridge Regression fallback.

Predicts one of 6 zones directly, matching the phrase board's 3-column x
2-row tile grid 1:1 (zone id == tile grid index) — no quadrant/sub-zone
translation layer.

Accuracy improvements:
  * Ridge alpha tuned to 0.5 (less regularisation — works better with the
    augmented + weighted calibration data)
  * Prediction smoothing: exponential moving average over recent predictions
    avoids single-frame glitches without adding heavy latency
"""

import logging
from enum import IntEnum
from typing import Optional
from collections import deque

import numpy as np
from gaze.features import weight_gaze_features

try:
    from sklearn.linear_model import RidgeClassifier
    from sklearn.preprocessing import StandardScaler
    SKLEARN_OK = True
except ImportError:
    SKLEARN_OK = False

try:
    import tensorflow as tf
    from tensorflow import keras
    TF_OK = True
except ImportError:
    TF_OK = False

logger = logging.getLogger(__name__)

if not SKLEARN_OK:
    logger.error(
        "scikit-learn is not installed/importable — gaze calibration will "
        "silently do nothing until it's installed (`pip install scikit-learn`)."
    )

N_FEATURES = 17
N_ZONES = 6  # matches the 3-col x 2-row tile grid, one class per tile slot


class GazeZone(IntEnum):
    NONE = -1


# ── Ridge Regression baseline ────────────────────────────────────────

class RidgeGazePredictor:
    """Scikit-learn Ridge classifier — works with very few calibration samples."""

    def __init__(self):
        self._scaler = StandardScaler() if SKLEARN_OK else None
        self._quad_model = RidgeClassifier(alpha=0.5) if SKLEARN_OK else None
        self._zone_model = RidgeClassifier(alpha=0.5) if SKLEARN_OK else None
        self._calibrated = False

    def calibrate(self, features: np.ndarray, quad_labels: np.ndarray,
                  zone_labels: Optional[np.ndarray] = None):
        """
        Fit on calibration data.

        Args:
            features:    (N, 17)
            quad_labels: (N,) ints 0-5
            zone_labels: (N,) ints 0-5  (optional, for fine zones)
        """
        if not SKLEARN_OK:
            logger.error("scikit-learn not available")
            return
        features = weight_gaze_features(features)
        X = self._scaler.fit_transform(features)
        self._quad_model.fit(X, quad_labels)
        if zone_labels is not None and len(np.unique(zone_labels)) > 1:
            self._zone_model.fit(X, zone_labels)
        self._calibrated = True
        logger.info("Ridge gaze model calibrated on %d samples", len(features))

    def predict_quadrant(self, features: np.ndarray) -> int:
        if not self._calibrated:
            return int(GazeZone.NONE)
        features = weight_gaze_features(features)
        X = self._scaler.transform(features.reshape(1, -1))
        return int(self._quad_model.predict(X)[0])

    def predict_zone(self, features: np.ndarray) -> int:
        if not self._calibrated:
            return int(GazeZone.NONE)
        features = weight_gaze_features(features)
        X = self._scaler.transform(features.reshape(1, -1))
        return int(self._zone_model.predict(X)[0])

    def predict_zone_proba(self, features: np.ndarray) -> np.ndarray:
        """Return decision function scores (not true probabilities) for
        all zones — used by the EMA smoother in GazePredictor."""
        if not self._calibrated:
            return np.zeros(N_ZONES)
        features = weight_gaze_features(features)
        X = self._scaler.transform(features.reshape(1, -1))
        scores = self._zone_model.decision_function(X)
        if scores.ndim == 1:
            return scores
        return scores[0]

    @property
    def is_calibrated(self) -> bool:
        return self._calibrated


# ── MLP gaze predictor ───────────────────────────────────────────────

class MLPGazePredictor:
    """TensorFlow/Keras MLP gaze predictor — more accurate with enough data."""

    def __init__(self):
        self._quad_model = None
        self._zone_model = None
        self._scaler = StandardScaler() if SKLEARN_OK else None
        self._calibrated = False

    def _build_model(self, n_classes: int):
        model = keras.Sequential([
            keras.layers.Input(shape=(N_FEATURES,)),
            keras.layers.Dense(64, activation="relu"),
            keras.layers.Dropout(0.3),
            keras.layers.Dense(32, activation="relu"),
            keras.layers.Dense(n_classes, activation="softmax"),
        ])
        model.compile(
            optimizer=keras.optimizers.Adam(learning_rate=0.001),
            loss="sparse_categorical_crossentropy",
            metrics=["accuracy"],
        )
        return model

    def calibrate(self, features: np.ndarray, quad_labels: np.ndarray,
                  zone_labels: Optional[np.ndarray] = None, epochs: int = 100):
        if not TF_OK or not SKLEARN_OK:
            logger.error("TensorFlow/sklearn not available for MLP gaze")
            return

        features = weight_gaze_features(features)
        X = self._scaler.fit_transform(features).astype(np.float32)

        self._quad_model = self._build_model(N_ZONES)
        self._quad_model.fit(X, quad_labels, epochs=epochs, verbose=0, batch_size=8)

        if zone_labels is not None and len(np.unique(zone_labels)) > 1:
            self._zone_model = self._build_model(N_ZONES)
            self._zone_model.fit(X, zone_labels, epochs=epochs, verbose=0, batch_size=8)

        self._calibrated = True
        logger.info("MLP gaze model calibrated on %d samples", len(features))

    def predict_quadrant(self, features: np.ndarray) -> int:
        if not self._calibrated or self._quad_model is None:
            return int(GazeZone.NONE)
        features = weight_gaze_features(features)
        X = self._scaler.transform(features.reshape(1, -1)).astype(np.float32)
        probs = self._quad_model.predict(X, verbose=0)[0]
        return int(np.argmax(probs))

    def predict_zone(self, features: np.ndarray) -> int:
        if not self._calibrated or self._zone_model is None:
            return self.predict_quadrant(features)
        features = weight_gaze_features(features)
        X = self._scaler.transform(features.reshape(1, -1)).astype(np.float32)
        probs = self._zone_model.predict(X, verbose=0)[0]
        return int(np.argmax(probs))

    @property
    def is_calibrated(self) -> bool:
        return self._calibrated


# ── Unified interface ────────────────────────────────────────────────

class GazePredictor:
    """
    Unified gaze predictor with temporal smoothing.

    Uses an exponential moving average (EMA) over Ridge decision-function
    scores to stabilise predictions.  This is superior to simple majority
    voting because it:
      * weighs recent frames more than old ones (lower latency)
      * is less susceptible to brief single-frame glitches
      * works with continuous scores, not just discrete votes
    """

    EMA_ALPHA = 0.35  # higher = more responsive, lower = more stable

    def __init__(self):
        self._ridge = RidgeGazePredictor() if SKLEARN_OK else None
        # MLP disabled: with only calibration-time data, Ridge is more robust
        self._mlp = None
        self._use_mlp = False
        # EMA score accumulator
        self._ema_scores: Optional[np.ndarray] = None

    def calibrate(self, features: np.ndarray, quad_labels: np.ndarray,
                  zone_labels: Optional[np.ndarray] = None):
        """Calibrate both predictors; prefer MLP if available."""
        self._ema_scores = None  # reset smoother on recalibration
        if self._ridge:
            self._ridge.calibrate(features, quad_labels, zone_labels)

        if self._mlp:
            try:
                self._mlp.calibrate(features, quad_labels, zone_labels)
                self._use_mlp = True
            except Exception as e:
                logger.warning("MLP calibration failed, using Ridge: %s", e)
                self._use_mlp = False

    def predict_quadrant(self, features: np.ndarray) -> int:
        if self._use_mlp and self._mlp and self._mlp.is_calibrated:
            return self._mlp.predict_quadrant(features)
        if self._ridge and self._ridge.is_calibrated:
            return self._ridge.predict_quadrant(features)
        return int(GazeZone.NONE)

    def predict_zone(self, features: np.ndarray) -> int:
        """Predict gaze zone with EMA temporal smoothing."""
        if self._use_mlp and self._mlp and self._mlp.is_calibrated:
            return self._mlp.predict_zone(features)

        if self._ridge and self._ridge.is_calibrated:
            raw_scores = self._ridge.predict_zone_proba(features)

            # Initialise EMA on first call
            if self._ema_scores is None:
                self._ema_scores = raw_scores.copy()
            else:
                self._ema_scores = (
                    self.EMA_ALPHA * raw_scores
                    + (1 - self.EMA_ALPHA) * self._ema_scores
                )
            return int(np.argmax(self._ema_scores))

        return int(GazeZone.NONE)

    @property
    def is_calibrated(self) -> bool:
        if self._use_mlp and self._mlp:
            return self._mlp.is_calibrated
        if self._ridge:
            return self._ridge.is_calibrated
        return False
