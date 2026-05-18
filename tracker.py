"""
tracker.py — Frame-level tracking layer for the video pipeline.

ObjectTracker: wraps one OpenCV CSRT tracker per detected object.
HumanZoneTracker: wraps MediaPipe Pose (Tasks API, 0.10+) to rebuild the
  safety zone polygon each frame.

Neither class calls Gemini. They run between Gemini detection cycles to keep
object positions and safety zones current at low cost.
"""

import os
import cv2
import numpy as np

# MediaPipe 0.10+ uses mp.tasks, not mp.solutions
try:
    import mediapipe as mp
    from mediapipe.tasks import python as _mp_python
    from mediapipe.tasks.python import vision as _mp_vision
    MEDIAPIPE_AVAILABLE = True
except ImportError:
    MEDIAPIPE_AVAILABLE = False
    print("[tracker] mediapipe not installed — human zone tracking will use static fallback")

DRIFT_THRESHOLD = 0.05   # normalized Euclidean distance
SAFETY_MARGIN   = 0.05   # outward hull expansion

# Shoulder, elbow, wrist, hip indices (MediaPipe Pose 33-landmark model)
_POSE_INDICES = [11, 12, 13, 14, 15, 16, 23, 24]

_MODEL_URL  = (
    "https://storage.googleapis.com/mediapipe-models/"
    "pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task"
)
_MODEL_PATH = os.path.join(os.path.dirname(__file__), "pose_landmarker_lite.task")


def _ensure_model() -> bool:
    if os.path.exists(_MODEL_PATH):
        return True
    try:
        import urllib.request
        print("[tracker] Downloading MediaPipe pose model (~3 MB)…")
        urllib.request.urlretrieve(_MODEL_URL, _MODEL_PATH)
        print("[tracker] Model ready.")
        return True
    except Exception as e:
        print(f"[tracker] Model download failed: {e} — falling back to static zone")
        return False


# ============================================================
# OBJECT TRACKER (OpenCV CSRT)
# ============================================================

class ObjectTracker:
    """Wraps a single OpenCV CSRT tracker for one detected object."""

    def __init__(self, object_id: str, bbox_xywh: tuple, frame):
        """
        object_id  : workspace object ID (e.g. "obj_001")
        bbox_xywh  : (x, y, w, h) in pixel space
        frame      : cv2 BGR frame at detection time
        """
        self.object_id = object_id
        self.lost = False
        self._bbox = tuple(int(v) for v in bbox_xywh)
        try:
            self._tracker = cv2.legacy.TrackerCSRT_create()
            self._tracker.init(frame, self._bbox)
        except AttributeError:
            self._tracker = None
            self.lost = True

    def update(self, frame) -> tuple[bool, tuple]:
        """Returns (success, (x, y, w, h)). Sets self.lost=True on failure."""
        if self._tracker is None:
            return False, self._bbox
        success, bbox = self._tracker.update(frame)
        if success:
            self._bbox = tuple(int(v) for v in bbox)
            self.lost = False
        else:
            self.lost = True
        return success, self._bbox

    def centroid_normalized(self, frame_w: int, frame_h: int) -> tuple[float, float]:
        x, y, w, h = self._bbox
        return round((x + w / 2) / frame_w, 4), round((y + h / 2) / frame_h, 4)

    def bbox_normalized(self, frame_w: int, frame_h: int) -> list[float]:
        x, y, w, h = self._bbox
        return [
            round(x / frame_w, 4),
            round(y / frame_h, 4),
            round((x + w) / frame_w, 4),
            round((y + h) / frame_h, 4),
        ]


# ============================================================
# HUMAN ZONE TRACKER (MediaPipe Tasks 0.10+)
# ============================================================

class HumanZoneTracker:
    """
    Updates the human safety zone polygon each frame using MediaPipe Pose.
    Falls back to a static polygon when MediaPipe is unavailable or no pose detected.
    """

    def __init__(self, static_polygon: list | None = None):
        self.static_polygon = static_polygon
        self._landmarker = None
        self._timestamp_ms = 0

        if not MEDIAPIPE_AVAILABLE:
            return
        if not _ensure_model():
            return

        try:
            base_opts = _mp_python.BaseOptions(model_asset_path=_MODEL_PATH)
            options = _mp_vision.PoseLandmarkerOptions(
                base_options=base_opts,
                running_mode=_mp_vision.RunningMode.VIDEO,
                num_poses=1,
                min_pose_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self._landmarker = _mp_vision.PoseLandmarker.create_from_options(options)
        except Exception as e:
            print(f"[tracker] PoseLandmarker init failed: {e}")

    def update(self, frame_bgr) -> list | None:
        """
        Returns updated polygon as list of {x, y} dicts (normalized),
        or self.static_polygon if no pose is detected.
        """
        if self._landmarker is None:
            return self.static_polygon

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        self._timestamp_ms += 33  # ~30 fps timestamps

        try:
            result = self._landmarker.detect_for_video(mp_image, self._timestamp_ms)
        except Exception:
            return self.static_polygon

        if not result.pose_landmarks:
            return self.static_polygon

        landmarks = result.pose_landmarks[0]
        points = []
        for idx in _POSE_INDICES:
            lm = landmarks[idx]
            if lm.visibility > 0.3:
                points.append([lm.x, lm.y])

        if len(points) < 3:
            return self.static_polygon

        pts = np.array(points, dtype=np.float32)
        hull = cv2.convexHull(pts, returnPoints=True).reshape(-1, 2)

        cx = float(np.mean(hull[:, 0]))
        cy = float(np.mean(hull[:, 1]))
        polygon = []
        for px, py in hull:
            dx, dy = px - cx, py - cy
            dist = (dx ** 2 + dy ** 2) ** 0.5
            if dist > 0:
                scale = (dist + SAFETY_MARGIN) / dist
                new_x, new_y = cx + dx * scale, cy + dy * scale
            else:
                new_x, new_y = px, py
            polygon.append({
                "x": round(min(1.0, max(0.0, float(new_x))), 4),
                "y": round(min(1.0, max(0.0, float(new_y))), 4),
            })

        self.static_polygon = polygon
        return polygon

    def close(self):
        if self._landmarker:
            self._landmarker.close()
            self._landmarker = None


# ============================================================
# HELPERS
# ============================================================

def check_drift(
    current: tuple[float, float],
    anchor: tuple[float, float],
    threshold: float = DRIFT_THRESHOLD,
) -> bool:
    """True if Euclidean distance between normalized centroids exceeds threshold."""
    dx = current[0] - anchor[0]
    dy = current[1] - anchor[1]
    return (dx * dx + dy * dy) ** 0.5 > threshold


def bbox_px_from_normalized(bbox_norm: dict, frame_w: int, frame_h: int) -> tuple:
    """
    Converts a workspace bounding_box (top_left/bottom_right normalized dicts)
    to pixel-space (x, y, w, h) for CSRT init.
    """
    tl = bbox_norm["top_left"]
    br = bbox_norm["bottom_right"]
    x1 = int(tl["x"] * frame_w)
    y1 = int(tl["y"] * frame_h)
    x2 = int(br["x"] * frame_w)
    y2 = int(br["y"] * frame_h)
    return (x1, y1, x2 - x1, y2 - y1)
