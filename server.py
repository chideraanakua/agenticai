"""
server.py — Phantom Limb Pipeline Server

Serves the browser demo and runs hybrid detection + planning.

Run:     python server.py
Demo:    http://localhost:8000

Install: pip install fastapi uvicorn google-generativeai opencv-python numpy Pillow
"""

import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from gemini_agents import (
    detect_objects_hybrid,
    plan_sorting,
)
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


def log(msg, level="info"):
    status_log.append({"msg": msg, "level": level})
    if len(status_log) > 200:
        status_log.pop(0)
    print(f"  [{level.upper()}] {msg}")


# ============================================================
# STATIC FILES — serves the browser demo
# ============================================================

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


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