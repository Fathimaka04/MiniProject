"""
GazeAssist Shared Perception Layer

The architectural centerpiece — a single MediaPipe Face Mesh instance shared
by all modules. No other module opens cv2.VideoCapture or runs its own vision
model. Modules consume landmarks via get_current_landmarks().
"""

import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable

import cv2
import numpy as np

try:
    import mediapipe as mp
    mp_face_mesh = mp.solutions.face_mesh
    MEDIAPIPE_AVAILABLE = True
except ImportError:
    MEDIAPIPE_AVAILABLE = False

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# MediaPipe Face Mesh Landmark Constants
# ──────────────────────────────────────────────────────────────────────

# EAR (Eye Aspect Ratio) computation points: [p1, p2, p3, p4, p5, p6]
RIGHT_EYE_EAR = [33, 160, 158, 133, 153, 144]
LEFT_EYE_EAR = [263, 387, 385, 362, 380, 373]

# Iris landmarks (require refine_landmarks=True, indices 468-477)
RIGHT_IRIS = [468, 469, 470, 471, 472]  # center=468
LEFT_IRIS = [473, 474, 475, 476, 477]   # center=473

# Eye corner landmarks
RIGHT_EYE_CORNERS = [33, 133]   # outer, inner
LEFT_EYE_CORNERS = [263, 362]   # outer, inner (note: 263 outer, 362 inner for left)

# Head pose reference points for solvePnP
HEAD_POSE_POINTS = [1, 199, 33, 263, 61, 291]
# 1=nose tip, 199=chin, 33=right eye outer, 263=left eye outer,
# 61=right mouth corner, 291=left mouth corner

# 3D canonical face model points (mm) for solvePnP
CANONICAL_FACE_3D = np.array([
    (0.0, 0.0, 0.0),             # Nose tip
    (0.0, -330.0, -65.0),        # Chin
    (-165.0, 170.0, -135.0),     # Right eye outer corner
    (165.0, 170.0, -135.0),      # Left eye outer corner
    (-150.0, -150.0, -125.0),    # Right mouth corner
    (150.0, -150.0, -125.0),     # Left mouth corner
], dtype=np.float64)


@dataclass
class PerceptionFrame:
    """Immutable snapshot of one perception cycle — all modules read this."""
    landmarks: Optional[list] = None          # MediaPipe landmark list (478 points)
    frame: Optional[np.ndarray] = None        # Raw BGR frame from camera
    frame_rgb: Optional[np.ndarray] = None    # RGB version
    timestamp: float = 0.0                    # time.time() when captured
    frame_width: int = 0
    frame_height: int = 0
    face_detected: bool = False


class SharedPerception:
    """
    Single shared MediaPipe Face Mesh perception layer.

    Opens ONE cv2.VideoCapture — the only camera instance in the entire app.
    Runs in its own thread at 30+ FPS. All modules consume landmarks from
    get_current_frame() without opening their own cameras.

    Usage:
        perception = SharedPerception()
        perception.start()
        ...
        frame = perception.get_current_frame()
        if frame.face_detected:
            landmarks = frame.landmarks
        ...
        perception.stop()
    """

    def __init__(self, camera_index: int = 0, target_fps: int = 30):
        self._camera_index = camera_index
        self._target_fps = target_fps
        self._frame_interval = 1.0 / target_fps

        # Thread-safe shared state
        self._lock = threading.Lock()
        self._current_frame = PerceptionFrame()

        # Thread management
        self._thread: Optional[threading.Thread] = None
        self._running = False

        # Callbacks for push-style notification
        self._callbacks: list[Callable[[PerceptionFrame], None]] = []
        self._callback_lock = threading.Lock()

        # Performance tracking
        self._fps_counter = 0
        self._fps_timer = time.time()
        self._actual_fps = 0.0

        # Camera and MediaPipe references (initialized in start)
        self._cap: Optional[cv2.VideoCapture] = None
        self._face_mesh = None

    def start(self) -> bool:
        """Start the perception loop in a background thread."""
        if self._running:
            logger.warning("Perception already running")
            return True

        if not MEDIAPIPE_AVAILABLE:
            logger.error("MediaPipe not available — cannot start perception")
            return False

        self._running = True
        self._thread = threading.Thread(target=self._perception_loop, daemon=True)
        self._thread.start()
        logger.info("Shared perception started")
        return True

    def stop(self):
        """Stop the perception loop and release resources."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        logger.info("Shared perception stopped")

    def get_current_frame(self) -> PerceptionFrame:
        """Get the latest perception frame (thread-safe read)."""
        with self._lock:
            return self._current_frame

    def register_callback(self, callback: Callable[[PerceptionFrame], None]):
        """Register a callback invoked on each new frame (in perception thread)."""
        with self._callback_lock:
            self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable[[PerceptionFrame], None]):
        """Remove a previously registered callback."""
        with self._callback_lock:
            self._callbacks = [cb for cb in self._callbacks if cb is not callback]

    @property
    def actual_fps(self) -> float:
        """Current measured FPS."""
        return self._actual_fps

    @property
    def is_running(self) -> bool:
        return self._running

    def _perception_loop(self):
        """Main perception loop — runs in dedicated thread."""
        # Open camera
        self._cap = cv2.VideoCapture(self._camera_index)
        if not self._cap.isOpened():
            logger.error(f"Failed to open camera at index {self._camera_index}")
            self._running = False
            return

        # Set camera properties for performance
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self._cap.set(cv2.CAP_PROP_FPS, self._target_fps)

        # Initialize MediaPipe Face Mesh
        self._face_mesh = mp_face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=1,
            refine_landmarks=True,  # REQUIRED for iris landmarks (468-477)
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

        logger.info(
            f"Camera opened: {int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
            f"{int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))} @ "
            f"{self._cap.get(cv2.CAP_PROP_FPS):.0f}fps"
        )

        try:
            while self._running:
                loop_start = time.time()

                success, frame = self._cap.read()
                if not success:
                    logger.warning("Failed to read frame from camera")
                    time.sleep(0.01)
                    continue

                h, w = frame.shape[:2]

                # Convert BGR → RGB (MediaPipe requires RGB)
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                # Zero-copy optimization: mark as not writeable
                rgb_frame.flags.writeable = False
                results = self._face_mesh.process(rgb_frame)
                rgb_frame.flags.writeable = True

                # Build perception frame
                pf = PerceptionFrame(
                    frame=frame,
                    frame_rgb=rgb_frame,
                    timestamp=time.time(),
                    frame_width=w,
                    frame_height=h,
                    face_detected=False,
                )

                if results.multi_face_landmarks:
                    pf.landmarks = results.multi_face_landmarks[0].landmark
                    pf.face_detected = True

                # Publish to shared state (thread-safe)
                with self._lock:
                    self._current_frame = pf

                # Notify callbacks
                with self._callback_lock:
                    for cb in self._callbacks:
                        try:
                            cb(pf)
                        except Exception as e:
                            logger.error(f"Callback error: {e}")

                # FPS tracking
                self._fps_counter += 1
                elapsed = time.time() - self._fps_timer
                if elapsed >= 1.0:
                    self._actual_fps = self._fps_counter / elapsed
                    self._fps_counter = 0
                    self._fps_timer = time.time()

                # Frame rate control
                processing_time = time.time() - loop_start
                sleep_time = self._frame_interval - processing_time
                if sleep_time > 0:
                    time.sleep(sleep_time)

        finally:
            # Release resources
            if self._face_mesh:
                self._face_mesh.close()
                self._face_mesh = None
            if self._cap:
                self._cap.release()
                self._cap = None
            logger.info("Camera and Face Mesh released")
