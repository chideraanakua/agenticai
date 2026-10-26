"""
server.py — Pipeline Server

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
import threading
import time
import numpy as np
import cv2
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
import uvicorn

from core.gemini_agents import (
    detect_objects_hybrid,
    plan_sorting,
    evaluate_plan,
    category_to_color,
    _path_crosses_any_zone,
    _in_any_zone,
)
from tracker import ObjectTracker, HumanZoneTracker, check_drift, bbox_px_from_normalized
from core import storage as db

db.init_db()

app = FastAPI(title="Pipeline")

def _client_ip(request: Request) -> str:
    """Real client IP, honoring X-Forwarded-For when behind ngrok / a reverse proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=_client_ip)
app.state.limiter = limiter


def _friendly_rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        {"error": "Too many scans — please wait an hour or run locally. See the README for setup."},
        status_code=429,
        headers={"Retry-After": "3600"},
    )


app.add_exception_handler(RateLimitExceeded, _friendly_rate_limit_handler)

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
_tracker_frame_size: tuple | None = None  # (w, h) CSRT was initialised at
_ws_clients: list[WebSocket] = []
MIN_BLOCKED_FRAMES = 3            # consecutive frames a path must be blocked before replanning
_blocked_streak: int = 0
# Guards tracker_pool / human_zone_tracker: frames are now tracked in worker threads,
# while _init_trackers (replan or scan) swaps them out.
_tracker_state_lock = threading.RLock()
# Latest sharper (640x480) frame from the browser, used only for Gemini re-detection.
_hi_frame_b64: str | None = None
_hi_frame_time: float = 0.0
HI_FRAME_MAX_AGE = 1.5            # seconds; older than this and objects may have moved


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
    """Thread-safe wrapper (same signature as before, so existing callers are unchanged)."""
    with _tracker_state_lock:
        _init_trackers_locked(workspace, frame)


def _init_trackers_locked(workspace: dict, frame):
    """Initialise CSRT trackers and MediaPipe pose tracker from a fresh workspace."""
    global tracker_pool, human_zone_tracker, current_step_index, last_replan_time, _tracker_frame_size

    if human_zone_tracker:
        human_zone_tracker.close()

    ws = workspace.get("workspace", {})
    h, w = frame.shape[:2]

    tracker_pool = {}
    for obj in ws.get("objects", []):
        bb = obj.get("bounding_box")
        if not bb:
            continue
        x, y, bw, bh = bbox_px_from_normalized(bb, w, h)
        if bw > 0 and bh > 0:
            tracker_pool[obj["id"]] = ObjectTracker(obj["id"], (x, y, bw, bh), frame)

    static_polygon = None
    for zone in ws.get("safety_zones", []):
        if zone.get("type") == "human_presence":
            static_polygon = zone.get("polygon")
            break

    human_zone_tracker = HumanZoneTracker(static_polygon=static_polygon)
    current_step_index = 0
    last_replan_time = time.time()  # grace period — no replan for REPLAN_COOLDOWN seconds after init
    _tracker_frame_size = (frame.shape[1], frame.shape[0])  # (w, h) for incoming frame resize
    log(f"Trackers initialised: {len(tracker_pool)} objects at {_tracker_frame_size[0]}x{_tracker_frame_size[1]}")


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

    # Detect on the sharper frame if a fresh one is cached; trackers are still
    # initialised from the 320x240 frame below (detection boxes are normalized).
    detect_src = frame_b64
    if _hi_frame_b64 and (time.time() - _hi_frame_time) <= HI_FRAME_MAX_AGE:
        detect_src = _hi_frame_b64

    async with _replan_lock:
        last_replan_time = time.time()
        log("Auto-replan: re-detecting workspace...", "replan")
        try:
            loop = asyncio.get_running_loop()
            prev = (current_workspace or {}).get("workspace", {}).get("objects", [])
            workspace = await loop.run_in_executor(
                None,
                lambda: detect_objects_hybrid(detect_src, video=True, prev_objects=prev),
            )
            if not workspace["workspace"]["objects"]:
                log("Replan aborted: 0 objects detected, keeping previous plan", "warn")
                return
            current_workspace = workspace

            frame = _decode_frame_b64(frame_b64)
            if frame is not None:
                _init_trackers(workspace, frame)

            plan = await loop.run_in_executor(
                None, lambda: plan_sorting(workspace, video=True)
            )
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
    return HTMLResponse("<p>Place index.html in static/ folder</p>")


# ============================================================
# API ENDPOINTS
# ============================================================

@app.post("/api/detect")
@limiter.limit("10/hour")
async def detect_endpoint(request: Request):
    global current_workspace, current_session_id

    body = await request.json()
    image_data = body.get("image")

    if not image_data:
        return JSONResponse({"error": "No image provided"}, status_code=400)

    try:
        log("Starting hybrid detection...")
        # Opt-in: only the video-mode scan sends source="video". Uploads and photo scans
        # send nothing, so for them this call is exactly what it was before.
        detect_kwargs = {"video": True} if body.get("source") == "video" else {}
        if detect_kwargs:
            log("Video-mode scan: using video detection prompt", "detect")
        workspace = detect_objects_hybrid(
            image_data,
            status_callback=lambda msg: log(msg, "detect"),
            **detect_kwargs,
        )

        current_workspace = workspace
        current_session_id = db.new_session()
        db.save_workspace(current_session_id, workspace)

        frame = _decode_frame_b64(image_data)
        if frame is not None:
            try:
                _init_trackers(workspace, frame)
            except Exception as track_err:
                log(f"Tracker init failed: {track_err}", "warn")

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
        past_sessions = db.get_rated_sessions(limit=10)
        plan = plan_sorting(
            current_workspace,
            past_sessions=past_sessions,
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


@app.post("/api/evaluate")
async def evaluate_endpoint():
    if not current_workspace or not current_plan:
        return JSONResponse(
            {"error": "No workspace or plan loaded. Run detection and planning first."},
            status_code=400,
        )
    past_sessions = db.get_rated_sessions(limit=10)
    result = evaluate_plan(current_workspace, current_plan, past_sessions)
    if current_session_id and result.get("predicted_score") is not None:
        db.save_evaluation(
            current_session_id,
            result["predicted_score"],
            result.get("critique", ""),
            result.get("suggestions", []),
        )
    return result


@app.get("/api/calibration")
async def get_calibration():
    return db.get_calibration_stats()


@app.get("/api/stats")
async def get_stats():
    return db.get_stats()


# ============================================================
# VIDEO TRACKING ENDPOINTS
# ============================================================

@app.websocket("/ws/unity")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _ws_clients.append(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive; we only push, never pull
    except WebSocketDisconnect:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)


def _track_frame_sync(frame):
    """Blocking per-frame tracking work; executed in a worker thread."""
    with _tracker_state_lock:
        return _track_frame_locked(frame)


def _track_frame_locked(frame):
    h, w = frame.shape[:2]
    positions, lost_objects = {}, []
    for obj_id, tracker in list(tracker_pool.items()):
        success, _ = tracker.update(frame)
        if success:
            cx, cy = tracker.centroid_normalized(w, h)
            positions[obj_id] = {
                "centroid": {"x": cx, "y": cy},
                "bbox": tracker.bbox_normalized(w, h),
            }
        if tracker.lost:
            lost_objects.append(obj_id)
    human_zone = human_zone_tracker.update(frame) if human_zone_tracker else None
    return positions, lost_objects, human_zone


@app.post("/api/video/frame")
async def video_frame_endpoint(request: Request):
    global last_replan_time, _blocked_streak, _hi_frame_b64, _hi_frame_time

    body = await request.json()
    frame_b64 = body.get("frame")
    if not frame_b64:
        return JSONResponse({"error": "No frame provided"}, status_code=400)

    frame_hi = body.get("frame_hi")
    if isinstance(frame_hi, str) and frame_hi:
        _hi_frame_b64, _hi_frame_time = frame_hi, time.time()

    frame = _decode_frame_b64(frame_b64)
    if frame is None:
        return JSONResponse({"error": "Could not decode frame"}, status_code=400)

    # Resize to match the resolution CSRT was initialised at (frontend sends 320x240)
    if _tracker_frame_size and (frame.shape[1], frame.shape[0]) != _tracker_frame_size:
        frame = cv2.resize(frame, _tracker_frame_size)

    h, w = frame.shape[:2]

    # Run CSRT + MediaPipe off the event loop so frames/websockets don't stall
    loop = asyncio.get_running_loop()
    positions, lost_objects, human_zone = await loop.run_in_executor(
        None, _track_frame_sync, frame
    )

    # Build current zones list with updated human polygon for path checks
    stale_steps, blocked_steps = [], []
    release_deferred = False
    if current_plan and current_workspace:
        ws = current_workspace.get("workspace", {})
        zones = list(ws.get("safety_zones", []))
        if human_zone:
            zones = [
                {**z, "polygon": human_zone} if z.get("type") == "human_presence" else z
                for z in zones
            ]

        human_zones = [z for z in zones if z.get("type") == "human_presence"]
        obj_anchors = {o["id"]: o["centroid"] for o in ws.get("objects", [])}
        sequence = current_plan.get("sequence", [])

        for step in sequence[current_step_index:]:
            step_idx = step.get("step", 1) - 1
            obj_id = step.get("object_id")

            if obj_id and obj_id in positions and obj_id in obj_anchors:
                anchor = obj_anchors[obj_id]
                cur = positions[obj_id]["centroid"]
                if check_drift((cur["x"], cur["y"]), (anchor["x"], anchor["y"])):
                    stale_steps.append(step_idx)

            if step.get("deferred") and obj_id in obj_anchors:
                # Waiting on a human zone: replan once the object is no longer inside one.
                pos = positions.get(obj_id, {}).get("centroid") or obj_anchors[obj_id]
                if not _in_any_zone(pos["x"], pos["y"], human_zones):
                    release_deferred = True

            frm = step.get("from", {})
            to = step.get("to", {})
            if frm and to and not step.get("skip"):
                if _path_crosses_any_zone(
                    frm.get("x", 0), frm.get("y", 0),
                    to.get("x", 0), to.get("y", 0),
                    zones,
                ):
                    blocked_steps.append(step_idx)

    cooldown_clear = (time.time() - last_replan_time) >= REPLAN_COOLDOWN
    _blocked_streak = _blocked_streak + 1 if blocked_steps else 0
    needs_replan = bool(
        stale_steps or lost_objects or release_deferred
        or _blocked_streak >= MIN_BLOCKED_FRAMES
    )
    replanning = False

    if needs_replan and cooldown_clear and not _replan_lock.locked():
        replanning = True
        asyncio.create_task(_auto_replan(frame_b64))

    return {
        "positions": positions,
        "human_zone": human_zone,
        "stale_steps": list(set(stale_steps)),
        "blocked_steps": list(set(blocked_steps)),
        "lost_objects": lost_objects,
        "replanning": replanning,
    }


@app.post("/api/step/complete")
async def step_complete(request: Request):
    global current_step_index
    body = await request.json()
    idx = body.get("step_index")
    if isinstance(idx, int) and idx == current_step_index:
        current_step_index += 1
    return {"step_index": current_step_index}


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print("\n" + "=" * 54)
    print("  Safe Arc — Pipeline Server")
    print("  " + "-" * 50)
    print("  Browser demo:  http://localhost:8000")
    print("  Dashboard:     http://localhost:8000/dashboard")
    print("  API state:     http://localhost:8000/api/state")
    print("  " + "-" * 50)
    print("  Gemini key:    " + (
        "SET" if os.environ.get("GEMINI_API_KEY") else "NOT SET"
    ))
    print("=" * 54 + "\n")
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port)