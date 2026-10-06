"""
GazeAssist - Geometric feature extraction from MediaPipe Face Mesh landmarks.

Extracts iris positions, eye-corner ratios, head-pose angles, and eye openness
into a compact feature vector consumed by the gaze regression predictor.

**Head-pose invariance** — The primary design goal is that the feature vector
should be stable when the user is looking at the same screen zone but their
head shifts slightly (translation, tilt).  We achieve this by:

1. Computing iris position *relative to the eye socket* (outer/inner corners),
   which is already translation-invariant.
2. Additionally computing a head-pose-compensated iris position where the
   raw iris-in-eye ratio is adjusted by the estimated pitch/yaw so that
   equal "looking left" at two different head yaw angles yields the same
   compensated value.
3. Weighting the compensated features more heavily than the raw ones.
"""

import math
import logging
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ── Landmark indices ─────────────────────────────────────────────────
RIGHT_IRIS_CENTER = 468
LEFT_IRIS_CENTER = 473
RIGHT_EYE_OUTER, RIGHT_EYE_INNER = 33, 133
LEFT_EYE_OUTER, LEFT_EYE_INNER = 263, 362

# 6-point rigid head-pose set
HEAD_POSE_INDICES = [1, 199, 33, 263, 61, 291]
CANONICAL_3D = np.array([
    (0.0, 0.0, 0.0),
    (0.0, -330.0, -65.0),
    (-165.0, 170.0, -135.0),
    (165.0, 170.0, -135.0),
    (-150.0, -150.0, -125.0),
    (150.0, -150.0, -125.0),
], dtype=np.float64)

# Eyelid mid-points (upper, lower) — used for a much more reliable
# VERTICAL gaze signal than distance from the corner-to-corner line.
RIGHT_LID_UP, RIGHT_LID_DOWN = 159, 145
LEFT_LID_UP, LEFT_LID_DOWN = 386, 374

# EAR indices (shared with blink/ear.py)
RIGHT_EYE_EAR = [33, 160, 158, 133, 153, 144]
LEFT_EYE_EAR = [263, 387, 385, 362, 380, 373]

# ── Feature-importance weights ───────────────────────────────────────
# 21-element weight vector, applied AFTER StandardScaler (see gaze/model.py).
#
# NOTE: the old code multiplied weights in BEFORE StandardScaler, which
# divides every column by its std — so the weights cancelled out and did
# nothing. They now act as a real prior on the Ridge model.
#
# The user keeps their head still and moves only their EYES, so the eye
# signals (lid-relative vertical position, both-eye averages) get the most
# weight and head pose is kept low — otherwise tiny head jitter during
# calibration gets learned as if it were gaze.
FEATURE_WEIGHTS = np.array([
    0.5, 0.5, 0.5, 0.5,      # raw iris position (rx, ry, lx, ly)      [0-3]
    0.5, 0.5, 0.5, 0.5,      # iris-corner distance ratios             [4-7]
    0.5, 0.5, 0.25,          # head pitch, yaw, roll                   [8-10]
    0.25, 0.25,              # EAR (right, left)                       [11-12]
    1.0, 1.0, 1.0, 1.0,      # head-pose-compensated iris              [13-16]
    1.5, 1.5,                # iris between upper/lower lid (r, l)     [17-18]
    2.5, 2.5,                # both-eye average horizontal / vertical  [19-20]
], dtype=np.float64)

N_FEATURES = len(FEATURE_WEIGHTS)


def weight_gaze_features(features: np.ndarray) -> np.ndarray:
    """Apply per-feature importance weights (call on SCALED features)."""
    return features * FEATURE_WEIGHTS


def _lm_px(landmark, w: int, h: int) -> np.ndarray:
    """Convert a normalised MediaPipe landmark to pixel coords."""
    return np.array([landmark.x * w, landmark.y * h], dtype=np.float64)


def _ear(landmarks, indices, w: int, h: int) -> float:
    """Eye Aspect Ratio for one eye."""
    pts = [_lm_px(landmarks[i], w, h) for i in indices]
    v1 = np.linalg.norm(pts[1] - pts[5])
    v2 = np.linalg.norm(pts[2] - pts[4])
    hor = np.linalg.norm(pts[0] - pts[3])
    return (v1 + v2) / (2.0 * hor) if hor > 0 else 0.0


# ── Public API ───────────────────────────────────────────────────────

def estimate_head_pose(
    landmarks, frame_w: int, frame_h: int
) -> tuple[float, float, float]:
    """
    Estimate head orientation (pitch, yaw, roll) in degrees using solvePnP.

    Returns:
        (pitch, yaw, roll) in degrees.  (0, 0, 0) on failure.
    """
    image_pts = np.array(
        [_lm_px(landmarks[i], frame_w, frame_h) for i in HEAD_POSE_INDICES],
        dtype=np.float64,
    )

    focal = float(frame_w)
    cx, cy = frame_w / 2.0, frame_h / 2.0
    cam_matrix = np.array(
        [[focal, 0, cx], [0, focal, cy], [0, 0, 1]], dtype=np.float64
    )
    dist = np.zeros((4, 1), dtype=np.float64)

    ok, rvec, tvec = cv2.solvePnP(
        CANONICAL_3D, image_pts, cam_matrix, dist, flags=cv2.SOLVEPNP_ITERATIVE
    )
    if not ok:
        return (0.0, 0.0, 0.0)

    rmat, _ = cv2.Rodrigues(rvec)
    angles, *_ = cv2.RQDecomp3x3(rmat)
    return float(angles[0]), float(angles[1]), float(angles[2])


def compute_iris_position(
    landmarks, frame_w: int, frame_h: int
) -> dict:
    """
    Normalised iris position inside each eye (0 = outer corner, 1 = inner corner).

    Returns dict with keys:
        right_x, right_y, left_x, left_y
    """
    def _ratio(iris_idx, outer_idx, inner_idx):
        iris = _lm_px(landmarks[iris_idx], frame_w, frame_h)
        outer = _lm_px(landmarks[outer_idx], frame_w, frame_h)
        inner = _lm_px(landmarks[inner_idx], frame_w, frame_h)
        eye_vec = inner - outer
        eye_len = np.linalg.norm(eye_vec)
        if eye_len < 1e-6:
            return 0.5, 0.5
        proj = np.dot(iris - outer, eye_vec) / (eye_len ** 2)
        # Perpendicular component (vertical)
        perp = iris - (outer + proj * eye_vec)
        perp_ratio = np.linalg.norm(perp) / eye_len
        # Sign: positive if iris is above eye-line
        cross = eye_vec[0] * (iris[1] - outer[1]) - eye_vec[1] * (iris[0] - outer[0])
        perp_signed = perp_ratio if cross < 0 else -perp_ratio
        return float(np.clip(proj, 0, 1)), float(perp_signed)

    rx, ry = _ratio(RIGHT_IRIS_CENTER, RIGHT_EYE_OUTER, RIGHT_EYE_INNER)
    lx, ly = _ratio(LEFT_IRIS_CENTER, LEFT_EYE_OUTER, LEFT_EYE_INNER)
    return {"right_x": rx, "right_y": ry, "left_x": lx, "left_y": ly}


def compute_iris_corner_distances(
    landmarks, frame_w: int, frame_h: int
) -> tuple[float, float, float, float]:
    """
    Distance ratios: iris-to-outer / eye-width, iris-to-inner / eye-width
    for each eye.
    """
    def _dists(iris_idx, outer_idx, inner_idx):
        iris = _lm_px(landmarks[iris_idx], frame_w, frame_h)
        outer = _lm_px(landmarks[outer_idx], frame_w, frame_h)
        inner = _lm_px(landmarks[inner_idx], frame_w, frame_h)
        eye_w = np.linalg.norm(inner - outer)
        if eye_w < 1e-6:
            return 0.5, 0.5
        d_out = np.linalg.norm(iris - outer) / eye_w
        d_in = np.linalg.norm(iris - inner) / eye_w
        return float(d_out), float(d_in)

    r_out, r_in = _dists(RIGHT_IRIS_CENTER, RIGHT_EYE_OUTER, RIGHT_EYE_INNER)
    l_out, l_in = _dists(LEFT_IRIS_CENTER, LEFT_EYE_OUTER, LEFT_EYE_INNER)
    return r_out, r_in, l_out, l_in


def compute_lid_vertical_ratio(landmarks, frame_w: int, frame_h: int) -> tuple[float, float]:
    """Iris position between upper and lower eyelid, measured along the
    axis perpendicular to the eye (so head roll doesn't distort it).

    0 = at the upper lid, 1 = at the lower lid. Looking down also lowers
    the upper lid, which moves this ratio in the same direction — so it's
    a strong vertical-gaze signal (the hardest axis for webcam trackers).
    """
    def _ratio(iris_idx, outer_idx, inner_idx, up_idx, down_idx):
        iris = _lm_px(landmarks[iris_idx], frame_w, frame_h)
        outer = _lm_px(landmarks[outer_idx], frame_w, frame_h)
        inner = _lm_px(landmarks[inner_idx], frame_w, frame_h)
        up = _lm_px(landmarks[up_idx], frame_w, frame_h)
        down = _lm_px(landmarks[down_idx], frame_w, frame_h)
        axis = inner - outer
        n = np.linalg.norm(axis)
        if n < 1e-6:
            return 0.5
        normal = np.array([-axis[1], axis[0]]) / n
        span = float(np.dot(down - up, normal))
        if abs(span) < 1e-6:
            return 0.5
        return float(np.dot(iris - up, normal) / span)

    rv = _ratio(RIGHT_IRIS_CENTER, RIGHT_EYE_OUTER, RIGHT_EYE_INNER,
                RIGHT_LID_UP, RIGHT_LID_DOWN)
    lv = _ratio(LEFT_IRIS_CENTER, LEFT_EYE_OUTER, LEFT_EYE_INNER,
                LEFT_LID_UP, LEFT_LID_DOWN)
    return rv, lv


def _compensate_iris_for_head_pose(
    iris: dict, pitch_deg: float, yaw_deg: float
) -> tuple[float, float, float, float]:
    """Adjust iris-in-eye ratios to remove head-pose-induced shift.

    When the head rotates, the eye socket moves but the iris also shifts
    inside the socket because the camera perspective changes.  This adds
    a small correction based on the estimated head pose so that a user
    looking at the *same screen zone* gets approximately the same feature
    values regardless of minor head rotation.

    The correction coefficients (0.005 per degree) were empirically chosen
    to match the typical iris-ratio drift observed with ±15° head movement
    on standard webcams.

    Returns:
        (right_x_comp, right_y_comp, left_x_comp, left_y_comp)
    """
    # Yaw causes horizontal shift; pitch causes vertical shift.
    # Coefficients are intentionally small — we only need to cancel the
    # *residual* drift that the eye-socket-relative ratio doesn't absorb.
    yaw_rad = math.radians(yaw_deg)
    pitch_rad = math.radians(pitch_deg)

    horiz_correction = 0.005 * yaw_deg   # positive yaw → looking right
    vert_correction = 0.005 * pitch_deg  # positive pitch → looking up

    rx_comp = float(np.clip(iris["right_x"] - horiz_correction, 0, 1))
    ry_comp = float(iris["right_y"] - vert_correction)
    lx_comp = float(np.clip(iris["left_x"] - horiz_correction, 0, 1))
    ly_comp = float(iris["left_y"] - vert_correction)

    return rx_comp, ry_comp, lx_comp, ly_comp


def extract_gaze_features(
    landmarks, frame_w: int, frame_h: int
) -> np.ndarray:
    """
    Build the 21-element feature vector consumed by the gaze predictor.

    Layout:
        [0-3]   iris normalised position   (right_x, right_y, left_x, left_y)
        [4-7]   iris-to-corner dist ratios  (r_out, r_in, l_out, l_in)
        [8-10]  head pose                   (pitch, yaw, roll)  — degrees
        [11]    right-eye EAR
        [12]    left-eye EAR
        [13-16] head-pose-compensated iris  (right_x, right_y, left_x, left_y)
        [17-18] iris between eyelids        (right_v, left_v)
        [19-20] both-eye average            (horizontal, vertical)
    """
    iris = compute_iris_position(landmarks, frame_w, frame_h)
    dists = compute_iris_corner_distances(landmarks, frame_w, frame_h)
    pose = estimate_head_pose(landmarks, frame_w, frame_h)
    r_ear = _ear(landmarks, RIGHT_EYE_EAR, frame_w, frame_h)
    l_ear = _ear(landmarks, LEFT_EYE_EAR, frame_w, frame_h)
    comp = _compensate_iris_for_head_pose(iris, pitch_deg=pose[0], yaw_deg=pose[1])
    rv, lv = compute_lid_vertical_ratio(landmarks, frame_w, frame_h)

    # Right eye's 0->1 runs image-left->right, left eye's runs the other
    # way, so mirror the left eye before averaging. Averaging both eyes
    # roughly halves landmark noise.
    h_mean = (iris["right_x"] + (1.0 - iris["left_x"])) / 2.0
    v_mean = (rv + lv) / 2.0

    return np.array([
        iris["right_x"], iris["right_y"], iris["left_x"], iris["left_y"],
        dists[0], dists[1], dists[2], dists[3],
        pose[0], pose[1], pose[2],
        r_ear, l_ear,
        comp[0], comp[1], comp[2], comp[3],
        rv, lv,
        h_mean, v_mean,
    ], dtype=np.float64)