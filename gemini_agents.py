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

GEMINI_MODEL = "gemini-2.5-flash"
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


def _parse_json_response(text):
    text = text.strip()
    text = re.sub(r'^```json\s*', '', text)
    text = re.sub(r'^```\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return json.loads(text)


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
        print(f"  [DETECT] {msg}")

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
            temperature=0.1,
            max_output_tokens=2048,
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

    status(
        f"Detection complete: {len(objects)} objects, "
        f"{len(safety_zones)} safety zones"
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
                temperature=0.2,
                max_output_tokens=2048,
            ),
        )
        plan = _parse_json_response(response.text)

        if "sequence" not in plan or not plan["sequence"]:
            raise ValueError("No sequence in plan")

        status(
            f"Gemini plan: {len(plan['sequence'])} steps, "
            f"strategy: {plan.get('strategy', 'unknown')}"
        )
        return plan

    except Exception as e:
        status(f"Gemini planning failed ({e}), using heuristic")
        return _heuristic_plan(objects, safety_zones)


def _heuristic_plan(objects, safety_zones):
    """Fallback: group by category, place left of safety zones."""
    safe_x_max = 0.60
    for zone in safety_zones:
        for pt in zone.get("polygon", []):
            safe_x_max = min(safe_x_max, pt["x"] - 0.08)

    categories = {}
    for obj in objects:
        cat = obj.get("category", "other")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(obj)

    sequence = []
    clusters = []
    step_num = 1
    y_slot = 0.12

    for cat_name, cat_objects in categories.items():
        target_x = 0.10
        members = []
        for obj in cat_objects:
            target = {
                "x": round(min(target_x, safe_x_max), 4),
                "y": round(y_slot, 4),
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
            target_x += 0.12
            step_num += 1

        clusters.append({
            "cluster_id": cat_name,
            "label": f"{cat_name.capitalize()} items",
            "target_zone": {"x": 0.10, "y": round(y_slot, 4)},
            "members": members,
        })
        y_slot += 0.25

    return {
        "strategy": "heuristic_category_grouping",
        "reasoning": (
            f"Grouped {len(categories)} categories. "
            f"All targets left of x={safe_x_max:.2f}."
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