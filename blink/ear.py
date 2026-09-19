import math
from typing import List, Tuple

# MediaPipe face mesh landmarks for eyes
# p1-p6 format for EAR calculation
RIGHT_EYE_EAR_INDICES = [33, 160, 158, 133, 153, 144]
LEFT_EYE_EAR_INDICES = [263, 387, 385, 362, 380, 373]

def euclidean_distance(p1, p2) -> float:
    """Calculate 3D Euclidean distance between two landmarks."""
    return math.sqrt((p1.x - p2.x)**2 + (p1.y - p2.y)**2 + (p1.z - p2.z)**2)

def compute_ear(landmarks: List, eye_indices: List[int]) -> float:
    """
    Compute the Eye Aspect Ratio (EAR) for a given eye.
    
    EAR = (|p2 - p6| + |p3 - p5|) / (2 * |p1 - p4|)
    """
    if len(landmarks) == 0:
        return 0.0
        
    p1 = landmarks[eye_indices[0]]
    p2 = landmarks[eye_indices[1]]
    p3 = landmarks[eye_indices[2]]
    p4 = landmarks[eye_indices[3]]
    p5 = landmarks[eye_indices[4]]
    p6 = landmarks[eye_indices[5]]
    
    # Verticals
    dist_2_6 = euclidean_distance(p2, p6)
    dist_3_5 = euclidean_distance(p3, p5)
    
    # Horizontal
    dist_1_4 = euclidean_distance(p1, p4)
    
    # Avoid division by zero
    if dist_1_4 == 0:
        return 0.0
        
    ear = (dist_2_6 + dist_3_5) / (2.0 * dist_1_4)
    return ear

def compute_both_ears(landmarks: List) -> Tuple[float, float, float]:
    """
    Compute EAR for both eyes.
    
    Returns:
        tuple[float, float, float]: (left_ear, right_ear, average_ear)
    """
    if not landmarks:
        return 0.0, 0.0, 0.0
        
    left_ear = compute_ear(landmarks, LEFT_EYE_EAR_INDICES)
    right_ear = compute_ear(landmarks, RIGHT_EYE_EAR_INDICES)
    avg_ear = (left_ear + right_ear) / 2.0
    
    return left_ear, right_ear, avg_ear
