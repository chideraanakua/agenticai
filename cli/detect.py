"""
detect_gemini.py — Vision pipeline using Gemini API
Sends a workspace photo to Gemini, gets back identified objects with positions.

Way more robust than color thresholding — detects ANY object regardless of color,
and gives semantic labels like "TV remote" instead of "blue_object".

Usage:
    python detect_gemini.py photo.png                    # prints JSON to console
    python detect_gemini.py photo.png -o workspace.json  # saves to file
    python detect_gemini.py photo.png --preview          # shows annotated image

Setup:
    pip install google-generativeai opencv-python numpy Pillow
    export GEMINI_API_KEY="your-key-here"
    
    Get your key from: https://aistudio.google.com/apikey (free tier works fine)
"""

import google.generativeai as genai
import cv2
import numpy as np
import json
import argparse
import sys
import os
import base64
import re
from datetime import datetime, timezone
from PIL import Image
from dotenv import load_dotenv
load_dotenv()


# ============================================================
# CONFIGURATION
# ============================================================

# Minimum bounding box area as fraction of image to keep
MIN_AREA_RATIO = 0.005

# Safety zone margin around detected humans (fraction of image)
SAFETY_MARGIN = 0.06

# Gemini model — flash is fast and cheap, plenty for this
GEMINI_MODEL = "gemini-2.5-flash"


# ============================================================
# GEMINI DETECTION PROMPT
# ============================================================

DETECTION_PROMPT = """You are a workspace object detection system for a robotic sorting pipeline.

Analyze this overhead photo of a workspace (table/desk) and identify ALL distinct objects visible on the surface.

For EACH object, provide:
1. A short descriptive label (e.g., "cookbook", "tv_remote", "whiskey_bottle", "pen", "glasses")
2. A category from: kitchen, reading, electronics, writing, tools, beverage, other
3. The bounding box as normalized coordinates (0.0 to 1.0) where top-left is (0,0) and bottom-right is (1,1)
4. Whether this is a human or human body part (hand, arm, foot)

Also identify any humans or human body parts visible (hands, arms, feet) — these become safety zones.

IMPORTANT RULES:
- Only detect objects ON the table/workspace surface
- Do NOT detect the table itself, walls, floor, or furniture (couch, chairs) around the table
- Estimate bounding boxes as accurately as possible
- Be specific with labels — "jim_beam_bottle" not just "bottle"

Respond ONLY with valid JSON, no markdown, no explanation:
{
    "objects": [
        {
            "label": "short_descriptive_name",
            "category": "one_of_the_categories",
            "bounding_box": {
                "x_min": 0.0,
                "y_min": 0.0,
                "x_max": 1.0,
                "y_max": 1.0
            }
        }
    ],
    "humans": [
        {
            "label": "description",
            "bounding_box": {
                "x_min": 0.0,
                "y_min": 0.0,
                "x_max": 1.0,
                "y_max": 1.0
            }
        }
    ]
}"""


# ============================================================
# CORE DETECTION
# ============================================================

def detect_objects_gemini(image_path):
    """
    Send image to Gemini, get back identified objects.
    Returns workspace JSON matching the locked pipeline schema.
    """
    # Check API key
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable not set.")
        print("Get a free key from: https://aistudio.google.com/apikey")
        print("Then run: export GEMINI_API_KEY='your-key-here'")
        sys.exit(1)

    # Configure Gemini
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(GEMINI_MODEL)

    # Load image
    img_cv = cv2.imread(image_path)
    if img_cv is None:
        print(f"Error: Could not load image '{image_path}'")
        sys.exit(1)
    h, w = img_cv.shape[:2]

    # Open with PIL for Gemini
    img_pil = Image.open(image_path)

    print(f"Sending image ({w}x{h}) to Gemini {GEMINI_MODEL}...")

    # Call Gemini with image
    response = model.generate_content(
        [DETECTION_PROMPT, img_pil],
        generation_config=genai.GenerationConfig(
            temperature=0.0,  # low temp for consistent structured output
            max_output_tokens=8192,
        ),
    )

    # Parse response
    raw_text = response.text.strip()

    # Clean markdown fences if present
    raw_text = re.sub(r'^```json\s*', '', raw_text)
    raw_text = re.sub(r'^```\s*', '', raw_text)
    raw_text = re.sub(r'\s*```$', '', raw_text)

    try:
        gemini_data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        print(f"Error: Gemini returned invalid JSON: {e}")
        print(f"Raw response:\n{raw_text}")
        sys.exit(1)

    # Convert Gemini output to our locked pipeline schema
    objects = []
    safety_zones = []
    obj_counter = 1

    for obj in gemini_data.get("objects", []):
        bb = obj.get("bounding_box", {})
        x_min = bb.get("x_min", 0)
        y_min = bb.get("y_min", 0)
        x_max = bb.get("x_max", 0)
        y_max = bb.get("y_max", 0)

        # Calculate area ratio
        area_ratio = (x_max - x_min) * (y_max - y_min)
        if area_ratio < MIN_AREA_RATIO:
            continue

        # Calculate centroid
        cx = (x_min + x_max) / 2
        cy = (y_min + y_max) / 2

        obj_id = f"obj_{obj_counter:03d}"
        obj_counter += 1

        objects.append({
            "id": obj_id,
            "label": obj.get("label", "unknown"),
            "category": obj.get("category", "other"),
            "centroid": {
                "x": round(cx, 4),
                "y": round(cy, 4),
            },
            "bounding_box": {
                "top_left": {
                    "x": round(x_min, 4),
                    "y": round(y_min, 4),
                },
                "bottom_right": {
                    "x": round(x_max, 4),
                    "y": round(y_max, 4),
                },
            },
            "area_ratio": round(area_ratio, 4),
            "confidence": 0.95,  # Gemini doesn't give confidence, but it's reliable
        })

    # Process human detections into safety zones
    zone_counter = 1
    for human in gemini_data.get("humans", []):
        bb = human.get("bounding_box", {})
        x_min = max(0, bb.get("x_min", 0) - SAFETY_MARGIN)
        y_min = max(0, bb.get("y_min", 0) - SAFETY_MARGIN)
        x_max = min(1, bb.get("x_max", 0) + SAFETY_MARGIN)
        y_max = min(1, bb.get("y_max", 0) + SAFETY_MARGIN)

        safety_zones.append({
            "id": f"zone_human_{zone_counter:02d}",
            "type": "human_presence",
            "polygon": [
                {"x": round(x_min, 4), "y": round(y_min, 4)},
                {"x": round(x_max, 4), "y": round(y_min, 4)},
                {"x": round(x_max, 4), "y": round(y_max, 4)},
                {"x": round(x_min, 4), "y": round(y_max, 4)},
            ],
            "risk_level": "high",
        })
        zone_counter += 1

    # Build workspace output
    workspace = {
        "workspace": {
            "frame_id": f"frame_{os.path.basename(image_path).split('.')[0]}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dimensions": {
                "width_px": w,
                "height_px": h,
            },
            "objects": objects,
            "safety_zones": safety_zones,
        }
    }

    return workspace, img_cv


# ============================================================
# PREVIEW (same as before — draws boxes on the image)
# ============================================================

# Category colors (BGR for OpenCV)
CATEGORY_COLORS = {
    "kitchen":     (0xDD, 0x8A, 0x37),  # blue-ish
    "reading":     (0x75, 0x9E, 0x1D),  # green
    "writing":     (0x7E, 0x53, 0xD8),  # pink
    "electronics": (0xCA, 0x5D, 0x5D),  # light blue
    "beverage":    (0x17, 0x75, 0xBA),  # orange
    "tools":       (0x5D, 0xCA, 0x7B),  # light green
    "other":       (0x80, 0x80, 0x80),  # gray
}


def draw_preview(img, workspace_data):
    """Draw bounding boxes, labels, and safety zones on the image."""
    preview = img.copy()
    h, w = preview.shape[:2]
    ws = workspace_data["workspace"]

    # Draw safety zones first
    for zone in ws["safety_zones"]:
        pts = zone["polygon"]
        poly = np.array([
            [int(p["x"] * w), int(p["y"] * h)] for p in pts
        ], dtype=np.int32)

        overlay = preview.copy()
        cv2.fillPoly(overlay, [poly], (0, 0, 255))
        cv2.addWeighted(overlay, 0.25, preview, 0.75, 0, preview)
        cv2.polylines(preview, [poly], True, (0, 0, 255), 2)
        cv2.putText(preview, f"SAFETY ZONE",
                     (int(pts[0]["x"] * w) + 10, int(pts[0]["y"] * h) + 25),
                     cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

    # Draw objects
    for obj in ws["objects"]:
        bb = obj["bounding_box"]
        x1 = int(bb["top_left"]["x"] * w)
        y1 = int(bb["top_left"]["y"] * h)
        x2 = int(bb["bottom_right"]["x"] * w)
        y2 = int(bb["bottom_right"]["y"] * h)

        color = CATEGORY_COLORS.get(obj["category"], (200, 200, 200))

        # Bounding box
        cv2.rectangle(preview, (x1, y1), (x2, y2), color, 2)

        # Label background
        label = f"{obj['label']} [{obj['category']}]"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(preview, (x1, y1 - th - 10), (x1 + tw + 6, y1), color, -1)
        cv2.putText(preview, label, (x1 + 3, y1 - 5),
                     cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        # Centroid dot
        cx = int(obj["centroid"]["x"] * w)
        cy = int(obj["centroid"]["y"] * h)
        cv2.circle(preview, (cx, cy), 5, color, -1)

    return preview


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Phantom Limb — Gemini-powered workspace detection"
    )
    parser.add_argument("image", help="Path to overhead workspace photo")
    parser.add_argument("-o", "--output", help="Save JSON to file")
    parser.add_argument("--preview", action="store_true",
                        help="Show annotated preview window")
    parser.add_argument("--save-preview", help="Save annotated image to file")

    args = parser.parse_args()

    # Run detection
    workspace, img = detect_objects_gemini(args.image)

    obj_count = len(workspace["workspace"]["objects"])
    zone_count = len(workspace["workspace"]["safety_zones"])
    print(f"\nDetected: {obj_count} objects, {zone_count} safety zones")

    # Print detected objects
    for obj in workspace["workspace"]["objects"]:
        print(f"  • {obj['label']} ({obj['category']}) at ({obj['centroid']['x']:.2f}, {obj['centroid']['y']:.2f})")

    for zone in workspace["workspace"]["safety_zones"]:
        print(f"  ⚠ Safety zone: {zone['id']}")

    # Output JSON
    json_str = json.dumps(workspace, indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(json_str)
        print(f"\nSaved JSON → {args.output}")
    else:
        print(f"\n{json_str}")

    # Preview
    if args.preview or args.save_preview:
        preview = draw_preview(img, workspace)

        if args.save_preview:
            cv2.imwrite(args.save_preview, preview)
            print(f"Saved preview → {args.save_preview}")

        if args.preview:
            cv2.namedWindow("Phantom Limb — Gemini Detection", cv2.WINDOW_NORMAL)
            cv2.imshow("Phantom Limb — Gemini Detection", preview)
            print("\nPress any key to close preview...")
            cv2.waitKey(0)
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()