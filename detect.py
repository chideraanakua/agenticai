"""
detect.py — Vision pipeline for Phantom Limb
Takes an overhead photo → outputs workspace JSON matching the locked schema.

Usage:
    python detect.py photo.jpg                    # prints JSON to console
    python detect.py photo.jpg -o workspace.json  # saves to file
    python detect.py photo.jpg --preview          # shows annotated image
    python detect.py photo.jpg --preview -o out.json  # both

Install:
    pip install opencv-python numpy

How it works:
    1. Loads the image and converts to HSV color space
    2. Detects colored objects via HSV thresholding + contour finding
    3. Detects potential human presence near edges (large skin-tone regions)
    4. Outputs normalized (0-1) coordinates in the locked JSON schema

Tuning:
    - If objects aren't detected, adjust the HSV ranges in COLOR_RANGES
    - If too much noise, increase MIN_AREA_RATIO
    - Use --preview to see what's being detected and adjust
"""

import cv2
import numpy as np
import json
import argparse
import sys
import os
from datetime import datetime, timezone


# ============================================================
# COLOR DEFINITIONS
# Adjust these HSV ranges for your specific objects.
# Use hsv_tuner() below to find the right values.
# HSV in OpenCV: H=0-179, S=0-255, V=0-255
# ============================================================

COLOR_RANGES = {
    "blue": {
        "lower": np.array([90, 80, 80]),
        "upper": np.array([130, 255, 255]),
        "category": "kitchen",      # default category for blue objects
    },
    "red_low": {
        "lower": np.array([0, 80, 80]),
        "upper": np.array([10, 255, 255]),
        "category": "reading",
        "display_name": "red",       # red wraps around in HSV, so we need two ranges
    },
    "red_high": {
        "lower": np.array([160, 80, 80]),
        "upper": np.array([179, 255, 255]),
        "category": "reading",
        "display_name": "red",
    },
    "green": {
        "lower": np.array([35, 80, 80]),
        "upper": np.array([85, 255, 255]),
        "category": "other",
    },
    "yellow": {
        "lower": np.array([20, 80, 80]),
        "upper": np.array([35, 255, 255]),
        "category": "reading",
    },
    "orange": {
        "lower": np.array([10, 80, 80]),
        "upper": np.array([20, 255, 255]),
        "category": "other",
    },
    "pink": {
        "lower": np.array([140, 40, 80]),
        "upper": np.array([170, 255, 255]),
        "category": "writing",
    },
    "purple": {
        "lower": np.array([130, 50, 50]),
        "upper": np.array([160, 255, 255]),
        "category": "other",
    },
}

# Skin tone detection for human/safety zone detection
SKIN_RANGES = [
    {"lower": np.array([0, 20, 70]),  "upper": np.array([20, 180, 255])},
    {"lower": np.array([0, 10, 100]), "upper": np.array([25, 160, 255])},
]

# Minimum object area as fraction of total image area
# Increase if getting too many small noise detections
MIN_AREA_RATIO = 0.003   # 0.3% of image

# Minimum area for skin detection to count as human presence
MIN_SKIN_AREA_RATIO = 0.02  # 2% of image

# How close to an edge (as fraction) for skin to trigger safety zone
EDGE_MARGIN = 0.35  # skin region centroid must be in outer 35% of frame


# ============================================================
# CORE DETECTION
# ============================================================

def detect_objects(image_path):
    """
    Main detection function.
    Returns the workspace JSON dict matching the locked schema.
    """
    # Load image
    img = cv2.imread(image_path)
    if img is None:
        print(f"Error: Could not load image '{image_path}'")
        sys.exit(1)

    h, w = img.shape[:2]
    total_area = h * w

    # Convert to HSV
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    # Apply slight blur to reduce noise
    hsv_blur = cv2.GaussianBlur(hsv, (5, 5), 0)

    objects = []
    obj_counter = 1

    # --- Detect colored objects ---
    seen_colors = {}  # track merged red ranges

    for color_name, config in COLOR_RANGES.items():
        mask = cv2.inRange(hsv_blur, config["lower"], config["upper"])

        # Clean up mask
        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)   # remove noise
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)  # fill gaps

        # Find contours
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        display_name = config.get("display_name", color_name)

        for contour in contours:
            area = cv2.contourArea(contour)
            area_ratio = area / total_area

            if area_ratio < MIN_AREA_RATIO:
                continue

            # Get bounding box
            x, y, bw, bh = cv2.boundingRect(contour)

            # Get centroid via moments
            M = cv2.moments(contour)
            if M["m00"] == 0:
                continue
            cx = M["m10"] / M["m00"]
            cy = M["m01"] / M["m00"]

            # Check if this overlaps with an existing detection of same display_name
            # (handles the red_low + red_high merge case)
            merged = False
            if display_name in seen_colors:
                for existing in objects:
                    if existing.get("_color") == display_name:
                        # Check overlap via centroid distance
                        ex_cx = existing["centroid"]["x"] * w
                        ex_cy = existing["centroid"]["y"] * h
                        dist = np.sqrt((cx - ex_cx)**2 + (cy - ex_cy)**2)
                        if dist < max(bw, bh) * 1.5:
                            # Merge: expand bounding box
                            ox = existing["bounding_box"]["top_left"]["x"] * w
                            oy = existing["bounding_box"]["top_left"]["y"] * h
                            ox2 = existing["bounding_box"]["bottom_right"]["x"] * w
                            oy2 = existing["bounding_box"]["bottom_right"]["y"] * h
                            nx = min(ox, x)
                            ny = min(oy, y)
                            nx2 = max(ox2, x + bw)
                            ny2 = max(oy2, y + bh)
                            existing["bounding_box"]["top_left"]["x"] = round(nx / w, 4)
                            existing["bounding_box"]["top_left"]["y"] = round(ny / h, 4)
                            existing["bounding_box"]["bottom_right"]["x"] = round(nx2 / w, 4)
                            existing["bounding_box"]["bottom_right"]["y"] = round(ny2 / h, 4)
                            existing["centroid"]["x"] = round((nx + nx2) / 2 / w, 4)
                            existing["centroid"]["y"] = round((ny + ny2) / 2 / h, 4)
                            existing["area_ratio"] = round(max(existing["area_ratio"], area_ratio), 4)
                            merged = True
                            break

            if merged:
                continue

            obj_id = f"obj_{obj_counter:03d}"
            obj_counter += 1

            obj = {
                "id": obj_id,
                "label": f"{display_name}_object",
                "category": config["category"],
                "centroid": {
                    "x": round(cx / w, 4),
                    "y": round(cy / h, 4),
                },
                "bounding_box": {
                    "top_left": {
                        "x": round(x / w, 4),
                        "y": round(y / h, 4),
                    },
                    "bottom_right": {
                        "x": round((x + bw) / w, 4),
                        "y": round((y + bh) / h, 4),
                    },
                },
                "area_ratio": round(area_ratio, 4),
                "confidence": round(min(0.65 + area_ratio * 20, 0.98), 2),
                "_color": display_name,  # internal, removed before output
            }
            objects.append(obj)
            seen_colors[display_name] = True

    # --- Detect safety zones (human presence via skin tone) ---
    safety_zones = detect_safety_zones(hsv_blur, w, h, total_area)

    # Clean internal fields
    for obj in objects:
        obj.pop("_color", None)

    # Build output
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

    return workspace, img


def detect_safety_zones(hsv, w, h, total_area):
    """
    Detect large skin-tone regions near frame edges.
    These likely indicate a human near the workspace.
    Returns a list of safety zone polygons.
    """
    combined_mask = np.zeros((h, w), dtype=np.uint8)

    for skin_range in SKIN_RANGES:
        mask = cv2.inRange(hsv, skin_range["lower"], skin_range["upper"])
        combined_mask = cv2.bitwise_or(combined_mask, mask)

    # Heavy morphology to merge skin patches into blobs
    kernel = np.ones((15, 15), np.uint8)
    combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, kernel)
    combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(combined_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    safety_zones = []
    zone_counter = 1

    for contour in contours:
        area = cv2.contourArea(contour)
        area_ratio = area / total_area

        if area_ratio < MIN_SKIN_AREA_RATIO:
            continue

        # Get centroid
        M = cv2.moments(contour)
        if M["m00"] == 0:
            continue
        cx = M["m10"] / M["m00"] / w
        cy = M["m01"] / M["m00"] / h

        # Check if near an edge
        near_edge = (
            cx < EDGE_MARGIN or
            cx > (1 - EDGE_MARGIN) or
            cy < EDGE_MARGIN or
            cy > (1 - EDGE_MARGIN)
        )

        if not near_edge:
            continue

        # Build safety polygon (expanded bounding box with margin)
        x, y, bw, bh = cv2.boundingRect(contour)
        margin_x = int(w * 0.05)  # 5% margin
        margin_y = int(h * 0.05)

        x1 = max(0, x - margin_x) / w
        y1 = max(0, y - margin_y) / h
        x2 = min(w, x + bw + margin_x) / w
        y2 = min(h, y + bh + margin_y) / h

        zone = {
            "id": f"zone_human_{zone_counter:02d}",
            "type": "human_presence",
            "polygon": [
                {"x": round(x1, 4), "y": round(y1, 4)},
                {"x": round(x2, 4), "y": round(y1, 4)},
                {"x": round(x2, 4), "y": round(y2, 4)},
                {"x": round(x1, 4), "y": round(y2, 4)},
            ],
            "risk_level": "high",
        }
        safety_zones.append(zone)
        zone_counter += 1

    return safety_zones


# ============================================================
# PREVIEW / VISUALIZATION
# ============================================================

# Color map for drawing (BGR format for OpenCV)
DRAW_COLORS = {
    "kitchen":  (0xDD, 0x8A, 0x37),   # blue-ish
    "reading":  (0x75, 0x9E, 0x1D),   # green
    "writing":  (0x7E, 0x53, 0xD8),   # pink
    "other":    (0x17, 0x75, 0xBA),   # orange
}

def draw_preview(img, workspace_data):
    """Draw bounding boxes, labels, and safety zones on the image."""
    preview = img.copy()
    h, w = preview.shape[:2]
    ws = workspace_data["workspace"]

    # Draw safety zones first (underneath)
    for zone in ws["safety_zones"]:
        pts = zone["polygon"]
        poly = np.array([
            [int(p["x"] * w), int(p["y"] * h)] for p in pts
        ], dtype=np.int32)

        # Transparent red overlay
        overlay = preview.copy()
        cv2.fillPoly(overlay, [poly], (0, 0, 255))
        cv2.addWeighted(overlay, 0.25, preview, 0.75, 0, preview)

        # Dashed border
        cv2.polylines(preview, [poly], True, (0, 0, 255), 2)

        # Label
        cv2.putText(preview, f"SAFETY ZONE ({zone['type']})",
                     (int(pts[0]["x"] * w) + 10, int(pts[0]["y"] * h) + 25),
                     cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

    # Draw objects
    for obj in ws["objects"]:
        bb = obj["bounding_box"]
        x1 = int(bb["top_left"]["x"] * w)
        y1 = int(bb["top_left"]["y"] * h)
        x2 = int(bb["bottom_right"]["x"] * w)
        y2 = int(bb["bottom_right"]["y"] * h)

        color = DRAW_COLORS.get(obj["category"], (200, 200, 200))

        # Bounding box
        cv2.rectangle(preview, (x1, y1), (x2, y2), color, 2)

        # Label background
        label = f"{obj['label']} ({obj['confidence']})"
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
# HSV TUNER — Run this to find color ranges for your objects
# ============================================================

def hsv_tuner(image_path):
    """
    Interactive HSV range finder.
    Run: python detect.py photo.jpg --tune
    
    Use the sliders to find the right HSV range for each object color.
    Note down the values and update COLOR_RANGES above.
    Press 'q' to quit.
    """
    img = cv2.imread(image_path)
    if img is None:
        print(f"Error: Could not load '{image_path}'")
        return

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    cv2.namedWindow("HSV Tuner", cv2.WINDOW_NORMAL)
    cv2.namedWindow("Mask", cv2.WINDOW_NORMAL)

    cv2.createTrackbar("H Low",  "HSV Tuner", 0, 179, lambda x: None)
    cv2.createTrackbar("H High", "HSV Tuner", 179, 179, lambda x: None)
    cv2.createTrackbar("S Low",  "HSV Tuner", 50, 255, lambda x: None)
    cv2.createTrackbar("S High", "HSV Tuner", 255, 255, lambda x: None)
    cv2.createTrackbar("V Low",  "HSV Tuner", 50, 255, lambda x: None)
    cv2.createTrackbar("V High", "HSV Tuner", 255, 255, lambda x: None)

    print("\nHSV Tuner running. Adjust sliders to isolate object colors.")
    print("Note down the values and update COLOR_RANGES in detect.py.")
    print("Press 'q' to quit.\n")

    while True:
        h_lo = cv2.getTrackbarPos("H Low", "HSV Tuner")
        h_hi = cv2.getTrackbarPos("H High", "HSV Tuner")
        s_lo = cv2.getTrackbarPos("S Low", "HSV Tuner")
        s_hi = cv2.getTrackbarPos("S High", "HSV Tuner")
        v_lo = cv2.getTrackbarPos("V Low", "HSV Tuner")
        v_hi = cv2.getTrackbarPos("V High", "HSV Tuner")

        lower = np.array([h_lo, s_lo, v_lo])
        upper = np.array([h_hi, s_hi, v_hi])

        mask = cv2.inRange(hsv, lower, upper)
        result = cv2.bitwise_and(img, img, mask=mask)

        cv2.imshow("HSV Tuner", img)
        cv2.imshow("Mask", result)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            print(f"\nFinal range:")
            print(f'  "lower": np.array([{h_lo}, {s_lo}, {v_lo}]),')
            print(f'  "upper": np.array([{h_hi}, {s_hi}, {v_hi}]),')
            break

    cv2.destroyAllWindows()


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Phantom Limb — Workspace object detection"
    )
    parser.add_argument("image", help="Path to overhead workspace photo")
    parser.add_argument("-o", "--output", help="Save JSON to file")
    parser.add_argument("--preview", action="store_true",
                        help="Show annotated preview window")
    parser.add_argument("--save-preview", help="Save annotated image to file")
    parser.add_argument("--tune", action="store_true",
                        help="Launch interactive HSV tuner")
    parser.add_argument("--min-area", type=float, default=0.003,
                        help="Min object area ratio (default: 0.003)")

    args = parser.parse_args()

    # HSV tuner mode
    if args.tune:
        hsv_tuner(args.image)
        return

    # Override min area if specified
    global MIN_AREA_RATIO
    MIN_AREA_RATIO = args.min_area

    # Run detection
    workspace, img = detect_objects(args.image)

    obj_count = len(workspace["workspace"]["objects"])
    zone_count = len(workspace["workspace"]["safety_zones"])
    print(f"\nDetected: {obj_count} objects, {zone_count} safety zones\n")

    # Output JSON
    json_str = json.dumps(workspace, indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(json_str)
        print(f"Saved JSON → {args.output}")
    else:
        print(json_str)

    # Preview
    if args.preview or args.save_preview:
        preview = draw_preview(img, workspace)

        if args.save_preview:
            cv2.imwrite(args.save_preview, preview)
            print(f"Saved preview → {args.save_preview}")

        if args.preview:
            cv2.namedWindow("Phantom Limb — Detection Preview", cv2.WINDOW_NORMAL)
            cv2.imshow("Phantom Limb — Detection Preview", preview)
            print("\nPress any key to close preview...")
            cv2.waitKey(0)
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()