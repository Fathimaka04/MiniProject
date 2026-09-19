from enum import Enum
import threading
from typing import List
import numpy as np

try:
    import tensorflow as tf
    from tensorflow.keras.models import Sequential, load_model as keras_load_model
    from tensorflow.keras.layers import LSTM, Dense
    TF_AVAILABLE = True
except ImportError:
    TF_AVAILABLE = False


class BlinkType(Enum):
    """Types of blinks detected by the system."""
    NONE = 0
    NATURAL = 1
    SHORT_DELIBERATE = 2
    LONG_DELIBERATE = 3


class RuleBasedBlinkDetector:
    """
    Rule-based blink detector using EAR threshold and timing.
    """
    def __init__(self, ear_threshold: float = 0.21):
        self.ear_threshold = ear_threshold
        self.is_closed = False
        self.close_start_time = 0.0
        self.lock = threading.Lock()

    def update(self, avg_ear: float, timestamp: float) -> BlinkType:
        """
        Update detector state and return detected blink type if any.
        """
        with self.lock:
            detected_blink = BlinkType.NONE
            
            if avg_ear < self.ear_threshold:
                if not self.is_closed:
                    self.is_closed = True
                    self.close_start_time = timestamp
            else:
                if self.is_closed:
                    duration = timestamp - self.close_start_time
                    
                    if duration < 0.200:
                        detected_blink = BlinkType.NATURAL
                    elif duration < 0.600:
                        detected_blink = BlinkType.SHORT_DELIBERATE
                    else:
                        detected_blink = BlinkType.LONG_DELIBERATE
                        
                    self.is_closed = False

            return detected_blink


class LSTMBlinkClassifier:
    """
    LSTM-based blink classifier using TensorFlow/Keras.
    """
    def __init__(self, window_size: int = 15):
        self.window_size = window_size
        self.model = None
        self.lock = threading.Lock()
        
        if TF_AVAILABLE:
            self._build_model()
            
    def _build_model(self):
        """Build the LSTM model architecture."""
        self.model = Sequential([
            LSTM(64, return_sequences=True, input_shape=(self.window_size, 1)),
            LSTM(32),
            Dense(3, activation='softmax')
        ])
        self.model.compile(optimizer='adam', loss='sparse_categorical_crossentropy', metrics=['accuracy'])
        
    def predict(self, ear_window: List[float]) -> BlinkType:
        """
        Predict blink type from a window of EAR values.
        """
        if not TF_AVAILABLE or self.model is None or len(ear_window) != self.window_size:
            return BlinkType.NONE
            
        with self.lock:
            # Reshape input for LSTM: (batch_size, time_steps, features)
            x = np.array(ear_window).reshape((1, self.window_size, 1))
            predictions = self.model.predict(x, verbose=0)
            pred_class = np.argmax(predictions[0])
            
            # Map predictions (assuming 0: None, 1: Short, 2: Long)
            # Natural blinks are usually handled by rule-based or filtered out before LSTM
            if pred_class == 1:
                return BlinkType.SHORT_DELIBERATE
            elif pred_class == 2:
                return BlinkType.LONG_DELIBERATE
            else:
                return BlinkType.NONE

    def train(self, ear_sequences: np.ndarray, labels: np.ndarray, epochs: int = 10):
        """Train the LSTM model on enrollment data."""
        if not TF_AVAILABLE or self.model is None:
            raise RuntimeError("TensorFlow is not available for training.")
            
        with self.lock:
            self.model.fit(ear_sequences, labels, epochs=epochs, batch_size=32, verbose=0)
            
    def save_model(self, path: str):
        """Save the trained model to disk."""
        if self.model is not None and TF_AVAILABLE:
            with self.lock:
                self.model.save(path)
                
    def load_model(self, path: str):
        """Load a trained model from disk."""
        if TF_AVAILABLE:
            with self.lock:
                self.model = keras_load_model(path)


class BlinkClassifier:
    """
    Unified blink classifier that tries LSTM first and falls back to rule-based.
    """
    def __init__(self, ear_threshold: float = 0.21, window_size: int = 15):
        self.rule_based = RuleBasedBlinkDetector(ear_threshold=ear_threshold)
        self.lstm = LSTMBlinkClassifier(window_size=window_size)
        self.window_size = window_size
        self.ear_buffer = []
        self.lock = threading.Lock()
        
    def is_model_loaded(self) -> bool:
        """Check if LSTM model is available and loaded."""
        return TF_AVAILABLE and self.lstm.model is not None
        
    def update(self, avg_ear: float, timestamp: float) -> BlinkType:
        """
        Update the classifier with a new EAR value and timestamp.
        """
        with self.lock:
            # Buffer the EAR value
            self.ear_buffer.append(avg_ear)
            if len(self.ear_buffer) > self.window_size:
                self.ear_buffer.pop(0)
            
            # Try LSTM if available
            if self.is_model_loaded() and len(self.ear_buffer) == self.window_size:
                blink_type = self.lstm.predict(self.ear_buffer)
                if blink_type != BlinkType.NONE:
                    # Clear buffer to avoid multiple triggers for the same blink
                    self.ear_buffer.clear()
                    return blink_type
            
            # Fall back to rule-based
            return self.rule_based.update(avg_ear, timestamp)
