"""
server.py — Phantom Limb Pipeline Server

Serves the browser demo and runs hybrid detection + planning.

Run:     python server.py
Demo:    http://localhost:8000

Install: pip install fastapi uvicorn google-generativeai opencv-python numpy Pillow
"""

import os
import base64
import io
import pathlib
import asyncio
import time
import numpy as np
import cv2
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw
import uvicorn

from gemini_agents import (
    detect_objects_hybrid,
    plan_sorting,
    category_to_color,
    _path_crosses_any_zone,
)
from tracker import ObjectTracker, HumanZoneTracker, check_drift
import storage as db

db.init_db()

app = FastAPI(title="Phantom Limb Pipeline")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# STATE
# ============================================================

current_workspace = None
current_plan = None
current_session_id = None
status_log = []

# ---- video tracking state ----
tracker_pool: dict = {}           # object_id → ObjectTracker
human_zone_tracker: HumanZoneTracker | None = None
current_step_index: int = 0       # which step the frontend animation is on
last_replan_time: float = 0.0
REPLAN_COOLDOWN: float = 8.0      # seconds between auto-replans
_replan_lock = asyncio.Lock()
_ws_clients: list[WebSocket] = []


def log(msg, level="info"):
    status_log.append({"msg": msg, "level": level})
    if len(status_log) > 200:
        status_log.pop(0)
    print(f"  [{level.upper()}] {msg}")


def _decode_frame_b64(frame_b64: str):
    raw = frame_b64.split(",", 1)[-1] if "," in frame_b64 else frame_b64
    img_array = np.frombuffer(base64.b64decode(raw), dtype=np.uint8)
    return cv2.imdecode(img_array, cv2.IMREAD_COLOR)


def _init_trackers(workspace: dict, frame):
    """Initialise CSRT trackers and MediaPipe pose tracker from a fresh workspace."""
    global tracker_pool, human_zone_tracker, current_step_index, last_replan_time

    if human_zone_tracker:
        human_zone_tracker.close()

    ws = workspace.get("workspace", {})
    h, w = frame.shape[:2]

    tracker_pool = {}
    for obj in ws.get("objects", []):
        bbox_px = obj.get("bbox_px")
        if not bbox_px:
            continue
        x1, y1, x2, y2 = bbox_px
        bw, bh = x2 - x1, y2 - y1
        if bw > 0 and bh > 0:
            tracker_pool[obj["id"]] = ObjectTracker(obj["id"], (x1, y1, bw, bh), frame)

    static_polygon = None
    for zone in ws.get("safety_zones", []):
        if zone.get("type") == "human_presence":
            static_polygon = zone.get("polygon")
            break

    human_zone_tracker = HumanZoneTracker(static_polygon=static_polygon)
    current_step_index = 0
    last_replan_time = time.time()  # grace period — no replan for REPLAN_COOLDOWN seconds after init
    log(f"Trackers initialised: {len(tracker_pool)} objects")


async def _push_to_clients(message: dict):
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


async def _auto_replan(frame_b64: str):
    """Re-detect and re-plan using the current frame. Rate-limited by REPLAN_COOLDOWN."""
    global current_workspace, current_plan, current_step_index, last_replan_time

    async with _replan_lock:
        last_replan_time = time.time()
        log("Auto-replan: re-detecting workspace...", "replan")
        try:
            loop = asyncio.get_event_loop()
            workspace = await loop.run_in_executor(None, detect_objects_hybrid, frame_b64)
            current_workspace = workspace

            frame = _decode_frame_b64(frame_b64)
            if frame is not None:
                _init_trackers(workspace, frame)

            plan = await loop.run_in_executor(None, plan_sorting, workspace)
            current_plan = plan
            current_step_index = 0

            log(f"Auto-replan complete: {len(plan.get('sequence', []))} steps", "replan")
            await _push_to_clients({
                "type": "replan",
                "plan": plan,
                "workspace": workspace,
                "resume_from_step": 0,
            })
        except Exception as e:
            log(f"Auto-replan failed: {e}", "error")


# ============================================================
# STATIC FILES — serves the browser demo
# ============================================================

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _save_session_images(session_id: str, image_b64: str, workspace: dict):
    b64 = image_b64.split(",", 1)[-1]
    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    w, h = img.size

    session_dir = pathlib.Path(STATIC_DIR) / "sessions" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    img.save(session_dir / "original.jpg", "JPEG", quality=85)

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    ws = workspace.get("workspace", workspace)
    for zone in ws.get("safety_zones", []):
        poly = [
            (p["x"] * w, p["y"] * h) if isinstance(p, dict) else (p[0] * w, p[1] * h)
            for p in zone.get("polygon", [])
        ]
        if len(poly) >= 3:
            draw.polygon(poly, fill=(255, 80, 80, 60), outline=(255, 80, 80, 200))

    annotated = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw2 = ImageDraw.Draw(annotated)
    for obj in ws.get("objects", []):
        bb = obj.get("bounding_box")
        if not bb:
            continue
        if isinstance(bb, dict):
            x1 = bb["top_left"]["x"] * w
            y1 = bb["top_left"]["y"] * h
            x2 = bb["bottom_right"]["x"] * w
            y2 = bb["bottom_right"]["y"] * h
        else:
            x1, y1, x2, y2 = bb[0] * w, bb[1] * h, bb[2] * w, bb[3] * h
        color = category_to_color(obj.get("category", "other"))
        draw2.rectangle([x1, y1, x2, y2], outline=color, width=2)
        draw2.text((x1 + 3, y1 + 3), obj.get("label", obj.get("id", "?")), fill=color)

    annotated.save(session_dir / "annotated.jpg", "JPEG", quality=85)
    base = f"/static/sessions/{session_id}"
    return f"{base}/original.jpg", f"{base}/annotated.jpg"


@app.get("/")
async def serve_demo():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse("<h1>Phantom Limb</h1><p>Place index.html in static/ folder</p>")


# ============================================================
# API ENDPOINTS
# ============================================================

@app.post("/api/detect")
async def detect_endpoint(request: Request):
    global current_workspace, current_session_id

    body = await request.json()
    image_data = body.get("image")

    if not image_data:
        return JSONResponse({"error": "No image provided"}, status_code=400)

    try:
        log("Starting hybrid detection...")
        workspace = detect_objects_hybrid(
            image_data,
            status_callback=lambda msg: log(msg, "detect"),
        )

        current_workspace = workspace
        current_session_id = db.new_session()
        db.save_workspace(current_session_id, workspace)

        frame = _decode_frame_b64(image_data)
        if frame is not None:
            _init_trackers(workspace, frame)

        try:
            orig_url, ann_url = _save_session_images(current_session_id, image_data, workspace)
        except Exception as img_err:
            log(f"Image save failed: {img_err}", "warn")
            orig_url, ann_url = None, None
        if orig_url:
            db.save_images(current_session_id, orig_url, ann_url)

        obj_count = len(workspace["workspace"]["objects"])
        zone_count = len(workspace["workspace"]["safety_zones"])
        log(f"Detection complete: {obj_count} objects, {zone_count} safety zones")

        return {**workspace, "session_id": current_session_id}

    except Exception as e:
        log(f"Detection error: {e}", "error")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/plan")
async def plan_endpoint():
    global current_plan

    if not current_workspace:
        return JSONResponse(
            {"error": "No workspace loaded. Run detection first."},
            status_code=400,
        )

    try:
        log("Generating sorting plan...")
        plan = plan_sorting(
            current_workspace,
            status_callback=lambda msg: log(msg, "plan"),
        )

        current_plan = plan
        if current_session_id:
            db.save_plan(current_session_id, plan)
        steps = plan.get("sequence", [])
        log(f"Plan ready: {len(steps)} steps")

        return plan

    except Exception as e:
        log(f"Planning error: {e}", "error")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/workspace")
async def receive_workspace(request: Request):
    """Direct workspace injection (for testing / feed_photo.py)."""
    global current_workspace
    data = await request.json()

    if "workspace" not in data:
        return JSONResponse({"error": "Missing 'workspace' key"}, status_code=400)

    current_workspace = data
    obj_count = len(data["workspace"].get("objects", []))
    return {"status": "ok", "objects": obj_count}


@app.get("/api/state")
async def get_state():
    ws = current_workspace.get("workspace", {}) if current_workspace else {}
    objects = ws.get("objects", [])
    return {
        "object_count": len(objects),
        "objects": [
            {"id": o["id"], "label": o.get("label", "?")}
            for o in objects
        ],
        "safety_zone_count": len(ws.get("safety_zones", [])),
        "has_plan": current_plan is not None,
        "recent_log": status_log[-20:],
    }


# ============================================================
# DASHBOARD & FEEDBACK ENDPOINTS
# ============================================================

@app.get("/dashboard")
async def serve_dashboard():
    path = os.path.join(STATIC_DIR, "dashboard.html")
    if os.path.exists(path):
        return FileResponse(path)
    return HTMLResponse("<h1>Dashboard not found</h1>", status_code=404)


@app.get("/api/sessions")
async def list_sessions():
    return {"sessions": db.get_sessions()}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    s = db.get_session(session_id)
    if not s:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    return s


@app.post("/api/feedback/{session_id}")
async def submit_feedback(session_id: str, request: Request):
    body = await request.json()
    rating = body.get("rating")
    comment = body.get("comment", "")
    if not isinstance(rating, int) or not (1 <= rating <= 5):
        return JSONResponse({"error": "rating must be integer 1–5"}, status_code=400)
    try:
        db.save_feedback(session_id, rating, comment)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"status": "ok", "session_id": session_id, "rating": rating}


@app.get("/api/stats")
async def get_stats():
    return db.get_stats()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print("\n" + "=" * 54)
    print("  PHANTOM LIMB — Pipeline Server")
    print("  " + "-" * 50)
    print("  Browser demo:  http://localhost:8000")
    print("  Dashboard:     http://localhost:8000/dashboard")
    print("  API state:     http://localhost:8000/api/state")
    print("  " + "-" * 50)
    print("  Gemini key:    " + (
        "SET" if os.environ.get("GEMINI_API_KEY") else "NOT SET"
    ))
    print("=" * 54 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)