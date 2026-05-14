"""
gemini_agents.py — Phantom Limb AI Agents

Agent 1 (Hybrid Detection):
    Gemini sees the full image → returns object names + rough bounding boxes
    OpenCV refines each rough box → snaps to precise contours via edge detection
    Result: pixel-perfect coordinates + semantic labels

Agent 2 (Sorting Planner):
    Takes workspace JSON → Gemini plans optimal sorting sequence
    Respects safety zones, minimizes moves, groups by category
    Falls back to heuristic if Gemini fails

Install:
    pip install google-generativeai opencv-python numpy Pillow
    export GEMINI_API_KEY="your-key-here"
"""

import google.generativeai as genai
import cv2
import numpy as np
import json
import os
import re
import base64
import traceback
from datetime import datetime, timezone
from PIL import Image
from io import BytesIO
from dotenv import load_dotenv
load_dotenv()

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
_client = None


def _get_client():
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY not set. "
                "Get a free key from https://aistudio.google.com/apikey"
            )
        genai.configure(api_key=api_key)
        _client = genai.GenerativeModel(GEMINI_MODEL)
    return _client


def _repair_json(text):
    """
    Best-effort repair for common Gemini JSON issues:
    - Trailing commas before } or ]
    - Truncated response: close any unclosed brackets/braces
    """
    # Remove trailing commas
    text = re.sub(r',\s*([}\]])', r'\1', text)

    # If truncated mid-response, close open structures
    open_braces = text.count('{') - text.count('}')
    open_brackets = text.count('[') - text.count(']')
    # Strip any dangling partial token at the end (incomplete string or value)
    text = re.sub(r',?\s*"[^"]*$', '', text)   # incomplete key
    text = re.sub(r',?\s*\{[^}]*$', '', text)   # incomplete nested object
    if open_brackets > 0:
        text += ']' * open_brackets
    if open_braces > 0:
        text += '}' * open_braces
    return text


def _parse_json_response(text):
    original = text
    text = text.strip()
    fence_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if fence_match:
        text = fence_match.group(1).strip()
    else:
        obj_match = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', text)
        if obj_match:
            text = obj_match.group(1)

    # First attempt: parse as-is
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Second attempt: after repair
    try:
        return json.loads(_repair_json(text))
    except json.JSONDecodeError as e:
        print(f"  [DEBUG] JSON parse failed after repair: {e}")
        print(f"  [DEBUG] Raw Gemini response ({len(original)} chars):\n{original[:1000]}")
        raise


# ================================================================
# AGENT 1: HYBRID DETECTION (Gemini labels + OpenCV precision)
# ================================================================

DETECTION_PROMPT = """You are a workspace object detection system for a robotic sorting pipeline.

Analyze this overhead photo of a workspace surface (table/desk).

For EACH distinct object ON the surface, provide:
1. A short descriptive label (e.g., "saucepan", "tv_remote", "cookbook", "glasses", "pen")
2. A category: kitchen, reading, electronics, writing, tools, beverage, or other
3. An APPROXIMATE bounding box as normalized coordinates (0.0 to 1.0)
   where (0,0) is top-left and (1,1) is bottom-right of the image

Also identify any humans or human body parts visible (hands, arms, feet, legs).

RULES:
- Only detect objects ON the workspace surface, not the table itself
- Do NOT detect furniture around the table (couches, chairs, shelves)
- Give your best estimate for bounding boxes — they do not need to be perfect
- Be specific with labels: "jim_beam_bottle" not just "bottle"

Respond ONLY with valid JSON, no markdown fences, no explanation:
{
    "objects": [
        {
            "label": "descriptive_name",
            "category": "category_name",
            "bbox": { "x_min": 0.0, "y_min": 0.0, "x_max": 1.0, "y_max": 1.0 }
        }
    ],
    "humans": [
        {
            "label": "description",
            "bbox": { "x_min": 0.0, "y_min": 0.0, "x_max": 1.0, "y_max": 1.0 }
        }
    ]
}"""


def _refine_bbox_with_opencv(img, rough_bbox, padding_ratio=0.15):
    """
    Takes a rough bounding box from Gemini and refines it using
    OpenCV edge detection to snap to the actual object contour.
    """
    h, w = img.shape[:2]
    x_min = rough_bbox.get("x_min", 0)
    y_min = rough_bbox.get("y_min", 0)
    x_max = rough_bbox.get("x_max", 1)
    y_max = rough_bbox.get("y_max", 1)

    box_w = x_max - x_min
    box_h = y_max - y_min
    pad_x = box_w * padding_ratio
    pad_y = box_h * padding_ratio

    search_x1 = int(max(0, (x_min - pad_x)) * w)
    search_y1 = int(max(0, (y_min - pad_y)) * h)
    search_x2 = int(min(1, (x_max + pad_x)) * w)
    search_y2 = int(min(1, (y_max + pad_y)) * h)

    if search_x2 - search_x1 < 10 or search_y2 - search_y1 < 10:
        return rough_bbox, 0.0

    crop = img[search_y1:search_y2, search_x1:search_x2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 30, 100)

    kernel = np.ones((3, 3), np.uint8)
    edges = cv2.dilate(edges, kernel, iterations=2)
    edges = cv2.erode(edges, kernel, iterations=1)

    contours, _ = cv2.findContours(
        edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    if not contours:
        return rough_bbox, 0.0

    largest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(largest)
    crop_area = crop.shape[0] * crop.shape[1]

    if area < crop_area * 0.05:
        return rough_bbox, 0.0

    cx, cy, cw, ch = cv2.boundingRect(largest)

    refined = {
        "x_min": round((search_x1 + cx) / w, 4),
        "y_min": round((search_y1 + cy) / h, 4),
        "x_max": round((search_x1 + cx + cw) / w, 4),
        "y_max": round((search_y1 + cy + ch) / h, 4),
    }

    refined_area = (
        (refined["x_max"] - refined["x_min"])
        * (refined["y_max"] - refined["y_min"])
    )
    if refined_area < 0.001 or refined_area > 0.8:
        return rough_bbox, 0.0

    return refined, area / crop_area


def _refine_human_zone(img, rough_bbox, margin=0.05):
    """Refines human detection with skin-tone + edge detection."""
    h, w = img.shape[:2]
    x_min = rough_bbox.get("x_min", 0)
    y_min = rough_bbox.get("y_min", 0)
    x_max = rough_bbox.get("x_max", 1)
    y_max = rough_bbox.get("y_max", 1)

    pad = 0.05
    sx1 = int(max(0, x_min - pad) * w)
    sy1 = int(max(0, y_min - pad) * h)
    sx2 = int(min(1, x_max + pad) * w)
    sy2 = int(min(1, y_max + pad) * h)

    if sx2 - sx1 < 10 or sy2 - sy1 < 10:
        return _make_safety_polygon(rough_bbox, margin)

    crop = img[sy1:sy2, sx1:sx2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)

    skin1 = cv2.inRange(hsv, np.array([0, 30, 80]), np.array([20, 180, 255]))
    skin2 = cv2.inRange(hsv, np.array([0, 20, 100]), np.array([18, 160, 255]))
    skin = cv2.bitwise_or(skin1, skin2)

    kernel = np.ones((9, 9), np.uint8)
    skin = cv2.morphologyEx(skin, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(
        skin, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    if contours:
        largest = max(contours, key=cv2.contourArea)
        cx, cy, cw, ch = cv2.boundingRect(largest)
        refined = {
            "x_min": (sx1 + cx) / w,
            "y_min": (sy1 + cy) / h,
            "x_max": (sx1 + cx + cw) / w,
            "y_max": (sy1 + cy + ch) / h,
        }
        return _make_safety_polygon(refined, margin)

    return _make_safety_polygon(rough_bbox, margin)


def _make_safety_polygon(bbox, margin=0.05):
    return [
        {"x": round(max(0, bbox["x_min"] - margin), 4),
         "y": round(max(0, bbox["y_min"] - margin), 4)},
        {"x": round(min(1, bbox["x_max"] + margin), 4),
         "y": round(max(0, bbox["y_min"] - margin), 4)},
        {"x": round(min(1, bbox["x_max"] + margin), 4),
         "y": round(min(1, bbox["y_max"] + margin), 4)},
        {"x": round(max(0, bbox["x_min"] - margin), 4),
         "y": round(min(1, bbox["y_max"] + margin), 4)},
    ]


def detect_objects_hybrid(image_source, status_callback=None):
    """
    Hybrid detection: Gemini identifies + rough locates,
    OpenCV refines coordinates.

    Args:
        image_source: file path (str) OR base64 string
        status_callback: optional function(message) for progress

    Returns:
        workspace dict matching the locked pipeline schema
    """
    def status(msg):
        if status_callback:
            status_callback(msg)
        else:
            print(f"[DETECT] {msg}")

    status("Loading image...")
    if isinstance(image_source, str) and os.path.isfile(image_source):
        img_cv = cv2.imread(image_source)
        img_pil = Image.open(image_source)
    else:
        raw = image_source
        if isinstance(raw, str):
            if "," in raw:
                raw = raw.split(",", 1)[1]
            raw = base64.b64decode(raw)
        img_array = np.frombuffer(raw, dtype=np.uint8)
        img_cv = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        img_pil = Image.open(BytesIO(raw))

    if img_cv is None:
        raise ValueError("Could not decode image")

    h, w = img_cv.shape[:2]

    status("Sending to Gemini for object identification...")
    model = _get_client()
    response = model.generate_content(
        [DETECTION_PROMPT, img_pil],
        generation_config=genai.GenerationConfig(
            temperature=0.0,
            max_output_tokens=8192,
        ),
    )

    gemini_data = _parse_json_response(response.text)
    raw_objects = gemini_data.get("objects", [])
    raw_humans = gemini_data.get("humans", [])

    status(
        f"Gemini found {len(raw_objects)} objects, "
        f"{len(raw_humans)} humans. Refining with OpenCV..."
    )

    objects = []
    obj_counter = 1

    for raw_obj in raw_objects:
        rough = raw_obj.get("bbox", {})
        if not rough:
            continue

        refined, coverage = _refine_bbox_with_opencv(img_cv, rough)

        cx = (refined["x_min"] + refined["x_max"]) / 2
        cy = (refined["y_min"] + refined["y_max"]) / 2
        area = (
            (refined["x_max"] - refined["x_min"])
            * (refined["y_max"] - refined["y_min"])
        )

        if area < 0.001:
            continue

        source = "opencv" if coverage > 0.05 else "gemini_estimate"
        obj_id = f"obj_{obj_counter:03d}"
        obj_counter += 1

        objects.append({
            "id": obj_id,
            "label": raw_obj.get("label", "unknown"),
            "category": raw_obj.get("category", "other"),
            "centroid": {"x": round(cx, 4), "y": round(cy, 4)},
            "bounding_box": {
                "top_left": {
                    "x": round(refined["x_min"], 4),
                    "y": round(refined["y_min"], 4),
                },
                "bottom_right": {
                    "x": round(refined["x_max"], 4),
                    "y": round(refined["y_max"], 4),
                },
            },
            "area_ratio": round(area, 4),
            "confidence": round(0.92 if source == "opencv" else 0.78, 2),
            "coord_source": source,
        })

        status(f"  {raw_obj.get('label', '?')} — refined by {source}")

    safety_zones = []
    zone_counter = 1
    for raw_human in raw_humans:
        rough = raw_human.get("bbox", {})
        if not rough:
            continue
        polygon = _refine_human_zone(img_cv, rough)
        safety_zones.append({
            "id": f"zone_human_{zone_counter:02d}",
            "type": "human_presence",
            "label": raw_human.get("label", "human"),
            "polygon": polygon,
            "risk_level": "high",
        })
        zone_counter += 1

    # Robot base sits at the back-left corner of the table.
    # Normalized coords: arm root ≈ (0.18, 0.10) → guard a box around it.
    safety_zones.append({
        "id": "zone_robot_base",
        "type": "robot_base",
        "label": "robot base",
        "polygon": [
            {"x": 0.05, "y": 0.0},
            {"x": 0.31, "y": 0.0},
            {"x": 0.31, "y": 0.26},
            {"x": 0.05, "y": 0.26},
        ],
        "risk_level": "high",
    })

    status(
        f"Detection complete: {len(objects)} objects, "
        f"{len(safety_zones)} safety zones (incl. robot base)"
    )

    return {
        "workspace": {
            "frame_id": f"frame_{datetime.now().strftime('%H%M%S')}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dimensions": {"width_px": w, "height_px": h},
            "objects": objects,
            "safety_zones": safety_zones,
        }
    }


# ================================================================
# AGENT 2: SORTING PLANNER (Gemini spatial reasoning)
# ================================================================

PLANNING_PROMPT = """You are a spatial sorting planner for an industrial robot arm.

Given a workspace with detected objects and safety zones, plan the optimal
pick-and-place sequence to sort objects into organized groups.

RULES:
1. Group objects by category (kitchen together, reading together, etc.)
2. All target positions MUST be OUTSIDE any safety zone polygon
3. Move largest/most central items first to clear workspace
4. Minimize total movement distance
5. Never route through a safety zone
6. Targets within workspace bounds (0.0 to 1.0)
7. Space placed objects at least 0.08 apart in normalized coords

Respond ONLY with valid JSON, no markdown, no explanation:
{
    "strategy": "brief description",
    "reasoning": "1-2 sentences why this sequence",
    "clusters": [
        {
            "cluster_id": "category_name",
            "label": "Human readable group name",
            "target_zone": { "x": 0.0, "y": 0.0 },
            "members": ["obj_001"]
        }
    ],
    "sequence": [
        {
            "step": 1,
            "action": "pick_and_place",
            "object_id": "obj_001",
            "from": { "x": 0.0, "y": 0.0 },
            "to": { "x": 0.0, "y": 0.0 },
            "reason": "why this move now"
        }
    ]
}"""


def plan_sorting(workspace_data, status_callback=None):
    """
    Gemini Agent 2: plans the sorting sequence.
    Falls back to heuristic if Gemini fails.
    """
    def status(msg):
        if status_callback:
            status_callback(msg)
        print(f"  [PLAN] {msg}")

    ws = workspace_data.get("workspace", workspace_data)
    objects = ws.get("objects", [])
    safety_zones = ws.get("safety_zones", [])

    if not objects:
        return {
            "strategy": "empty",
            "reasoning": "No objects detected",
            "clusters": [],
            "sequence": [],
        }

    status("Sending workspace to Gemini for sorting plan...")

    workspace_summary = json.dumps({
        "objects": [
            {
                "id": o["id"],
                "label": o["label"],
                "category": o["category"],
                "centroid": o["centroid"],
            }
            for o in objects
        ],
        "safety_zones": [
            {"id": z["id"], "polygon": z["polygon"]}
            for z in safety_zones
        ],
    }, indent=2)

    prompt = f"{PLANNING_PROMPT}\n\nWorkspace state:\n{workspace_summary}"

    try:
        model = _get_client()
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                temperature=0.0,
                max_output_tokens=8192,
            ),
        )
        plan = _parse_json_response(response.text)

        if "sequence" not in plan or not plan["sequence"]:
            raise ValueError("No sequence in plan")

        status(
            f"Gemini plan: {len(plan['sequence'])} steps, "
            f"strategy: {plan.get('strategy', 'unknown')}"
        )

    except Exception as e:
        status(f"Gemini planning failed ({e}), using heuristic")
        plan = _heuristic_plan(objects, safety_zones)

    plan, relocated = _enforce_safety(plan, safety_zones)
    skipped = sum(1 for s in plan.get("sequence", []) if s.get("skip"))
    if relocated:
        status(f"Safety enforcement: relocated {relocated} target(s) outside safety zones")
    if skipped:
        status(f"Safety enforcement: skipped {skipped} step(s) — no safe path available")
    return plan


def _segments_intersect(p1, p2, p3, p4):
    """Return True if segment p1-p2 properly intersects segment p3-p4."""
    def cross2d(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    d1 = cross2d(p3, p4, p1)
    d2 = cross2d(p3, p4, p2)
    d3 = cross2d(p1, p2, p3)
    d4 = cross2d(p1, p2, p4)

    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
       ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True

    def on_seg(p, q, r):
        return (min(p[0], r[0]) <= q[0] <= max(p[0], r[0]) and
                min(p[1], r[1]) <= q[1] <= max(p[1], r[1]))

    if d1 == 0 and on_seg(p3, p1, p4): return True
    if d2 == 0 and on_seg(p3, p2, p4): return True
    if d3 == 0 and on_seg(p1, p3, p2): return True
    if d4 == 0 and on_seg(p1, p4, p2): return True
    return False


def _path_crosses_any_zone(x1, y1, x2, y2, safety_zones):
    """True if the straight-line path (x1,y1)→(x2,y2) enters any safety zone."""
    if _in_any_zone(x1, y1, safety_zones) or _in_any_zone(x2, y2, safety_zones):
        return True
    p1, p2 = (x1, y1), (x2, y2)
    for zone in safety_zones:
        poly = zone.get("polygon", [])
        n = len(poly)
        for i in range(n):
            j = (i + 1) % n
            if _segments_intersect(p1, p2,
                                   (poly[i]["x"], poly[i]["y"]),
                                   (poly[j]["x"], poly[j]["y"])):
                return True
    return False


def _point_in_polygon(x, y, polygon):
    """Ray-casting point-in-polygon test (normalized coords)."""
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]["x"], polygon[i]["y"]
        xj, yj = polygon[j]["x"], polygon[j]["y"]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _in_any_zone(x, y, safety_zones):
    return any(
        _point_in_polygon(x, y, z.get("polygon", []))
        for z in safety_zones
        if z.get("polygon")
    )


def _enforce_safety(plan, safety_zones):
    """
    Hard-constraint pass applied after both Gemini and heuristic planners.

    For every step, two conditions must hold:
      1. The destination ('to') is outside every safety zone.
      2. The straight-line carry path from the object's current position
         ('from') to 'to' does not cross any safety zone polygon.

    If either condition fails, we scan safe_candidates for a replacement
    destination that satisfies both. If none exists the step is marked
    skip=True and will not be executed.
    """
    if not safety_zones or not plan.get("sequence"):
        return plan, 0

    sx_min, sx_max, sy_min, sy_max = _compute_safe_area(safety_zones)
    candidates = _generate_grid_positions(
        len(plan["sequence"]) * 6, sx_min, sx_max, sy_min, sy_max
    )
    # Pre-filter: destination must be outside every zone
    safe_candidates = [
        c for c in candidates if not _in_any_zone(c["x"], c["y"], safety_zones)
    ]

    used = []
    relocated = 0
    skipped = 0

    for step in plan["sequence"]:
        frm = step.get("from", {})
        to = step["to"]
        fx, fy = frm.get("x", to["x"]), frm.get("y", to["y"])

        dest_unsafe = _in_any_zone(to["x"], to["y"], safety_zones)
        path_unsafe = _path_crosses_any_zone(fx, fy, to["x"], to["y"], safety_zones)

        if dest_unsafe or path_unsafe:
            placed = False
            for cand in safe_candidates:
                if any(
                    abs(cand["x"] - p["x"]) < 0.08 and abs(cand["y"] - p["y"]) < 0.08
                    for p in used
                ):
                    continue
                # Candidate destination must also have a safe carry path
                if _path_crosses_any_zone(fx, fy, cand["x"], cand["y"], safety_zones):
                    continue
                step["to"] = cand
                step["reason"] = (step.get("reason") or "") + " [safety enforced]"
                used.append(cand)
                relocated += 1
                placed = True
                break

            if not placed:
                step["skip"] = True
                step["reason"] = (
                    (step.get("reason") or "")
                    + " [skipped: no safe path available]"
                )
                skipped += 1
        else:
            used.append(to)

    # Also fix cluster target_zones
    for cluster in plan.get("clusters", []):
        tz = cluster.get("target_zone", {})
        if tz and _in_any_zone(tz["x"], tz["y"], safety_zones):
            for cand in safe_candidates:
                if not any(
                    abs(cand["x"] - p["x"]) < 0.08 and abs(cand["y"] - p["y"]) < 0.08
                    for p in used
                ):
                    cluster["target_zone"] = cand
                    used.append(cand)
                    break

    return plan, relocated


def _compute_safe_area(safety_zones):
    """
    Determines the safe rectangular region by detecting which side of
    the workspace each safety zone occupies.

    A zone at the bottom shrinks safe_y_max.
    A zone on the right shrinks safe_x_max.
    A zone on the left grows safe_x_min.
    A zone at the top grows safe_y_min.

    This prevents the bug where a bottom-side zone (e.g. human feet)
    was incorrectly shrinking the x boundary.
    """
    safe_x_min, safe_x_max = 0.02, 0.95
    safe_y_min, safe_y_max = 0.02, 0.95

    for zone in safety_zones:
        pts = zone.get("polygon", [])
        if not pts:
            continue

        zx_min = min(p["x"] for p in pts)
        zx_max = max(p["x"] for p in pts)
        zy_min = min(p["y"] for p in pts)
        zy_max = max(p["y"] for p in pts)
        zcx = (zx_min + zx_max) / 2
        zcy = (zy_min + zy_max) / 2

        if zcy > 0.55 and zy_min > 0.35:
            # Zone is at the bottom — shrink safe y ceiling
            safe_y_max = min(safe_y_max, zy_min - 0.06)
        elif zcy < 0.45 and zy_max < 0.65:
            # Zone is at the top — raise safe y floor
            safe_y_min = max(safe_y_min, zy_max + 0.06)

        if zcx > 0.55 and zx_min > 0.35:
            # Zone is on the right — shrink safe x ceiling
            safe_x_max = min(safe_x_max, zx_min - 0.06)
        elif zcx < 0.45 and zx_max < 0.65:
            # Zone is on the left — raise safe x floor
            safe_x_min = max(safe_x_min, zx_max + 0.06)

    # Clamp to valid workspace bounds
    safe_x_min = max(0.02, min(safe_x_min, 0.5))
    safe_x_max = min(0.98, max(safe_x_max, safe_x_min + 0.2))
    safe_y_min = max(0.02, min(safe_y_min, 0.5))
    safe_y_max = min(0.98, max(safe_y_max, safe_y_min + 0.2))

    return safe_x_min, safe_x_max, safe_y_min, safe_y_max


def _generate_grid_positions(n, safe_x_min, safe_x_max, safe_y_min, safe_y_max):
    """
    Generates n unique non-overlapping target positions arranged in a grid
    within the safe area. Objects are spaced at least MIN_SPACING apart.
    """
    MIN_SPACING = 0.11
    avail_w = safe_x_max - safe_x_min
    avail_h = safe_y_max - safe_y_min

    cols = max(1, int(avail_w / MIN_SPACING))
    rows = max(1, int(avail_h / MIN_SPACING))

    col_step = avail_w / cols
    row_step = avail_h / rows

    positions = []
    for row in range(rows):
        for col in range(cols):
            x = safe_x_min + col * col_step + col_step * 0.5
            y = safe_y_min + row * row_step + row_step * 0.5
            positions.append({
                "x": round(min(x, safe_x_max - 0.02), 4),
                "y": round(min(y, safe_y_max - 0.02), 4),
            })
            if len(positions) >= n:
                return positions

    # If grid ran out of cells, extend by adding more rows
    extra_row = rows
    while len(positions) < n:
        for col in range(cols):
            x = safe_x_min + col * col_step + col_step * 0.5
            y = safe_y_min + extra_row * row_step + row_step * 0.5
            positions.append({
                "x": round(min(x, safe_x_max - 0.02), 4),
                "y": round(min(y, 0.97), 4),
            })
            if len(positions) >= n:
                return positions
        extra_row += 1
        if extra_row > 20:
            break

    return positions


def _heuristic_plan(objects, safety_zones):
    """
    Fallback planner: group by category, assign unique non-overlapping
    grid positions within the computed safe area.
    """
    sx_min, sx_max, sy_min, sy_max = _compute_safe_area(safety_zones)

    positions = _generate_grid_positions(
        len(objects), sx_min, sx_max, sy_min, sy_max
    )

    categories = {}
    for obj in objects:
        cat = obj.get("category", "other")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(obj)

    sequence = []
    clusters = []
    step_num = 1
    pos_idx = 0

    for cat_name, cat_objects in categories.items():
        members = []
        cluster_anchor = (
            positions[pos_idx] if pos_idx < len(positions)
            else {"x": sx_min + 0.05, "y": sy_min + 0.05}
        )

        for obj in cat_objects:
            if pos_idx < len(positions):
                target = positions[pos_idx]
                pos_idx += 1
            else:
                target = {
                    "x": round(sx_min + 0.05, 4),
                    "y": round(sy_min + step_num * 0.06, 4),
                }

            sequence.append({
                "step": step_num,
                "action": "pick_and_place",
                "object_id": obj["id"],
                "from": obj["centroid"],
                "to": target,
                "reason": f"Group {cat_name} items",
            })
            members.append(obj["id"])
            step_num += 1

        clusters.append({
            "cluster_id": cat_name,
            "label": f"{cat_name.capitalize()} items",
            "target_zone": cluster_anchor,
            "members": members,
        })

    return {
        "strategy": "heuristic_category_grouping",
        "reasoning": (
            f"Grouped {len(categories)} categories into safe area "
            f"x=[{sx_min:.2f}, {sx_max:.2f}] y=[{sy_min:.2f}, {sy_max:.2f}]. "
            f"Each object assigned a unique grid position."
        ),
        "clusters": clusters,
        "sequence": sequence,
    }


def category_to_color(category):
    return {
        "kitchen": "blue", "reading": "green", "writing": "pink",
        "electronics": "cyan", "beverage": "orange",
        "tools": "yellow", "other": "gray",
    }.get(category, "gray")