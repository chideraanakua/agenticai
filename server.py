"""
server_v3.py — Phantom Limb Pipeline Server

Serves the browser demo, runs hybrid detection + planning,
and maintains backward compatibility with Unity via WebSocket.

Run:     python server_v3.py
Demo:    http://localhost:8000
Unity:   ws://localhost:8000/ws/unity

Install: pip install fastapi uvicorn websockets google-generativeai opencv-python numpy Pillow
"""

import asyncio
import json
import os
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from gemini_agents import (
    detect_objects_hybrid,
    plan_sorting,
    category_to_color,
)

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
unity_ws = None
unity_connected = False
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
    """
    Accept an image (base64), run hybrid detection (Gemini + OpenCV),
    return workspace JSON, and push to Unity if connected.
    """
    global current_workspace

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
        obj_count = len(workspace["workspace"]["objects"])
        zone_count = len(workspace["workspace"]["safety_zones"])
        log(f"Detection complete: {obj_count} objects, {zone_count} safety zones")

        if unity_ws and unity_connected:
            ws_data = workspace["workspace"].copy()
            for obj in ws_data.get("objects", []):
                if "color" not in obj:
                    obj["color"] = category_to_color(
                        obj.get("category", "other")
                    )
            await unity_ws.send_json({
                "type": "workspace_init",
                "data": ws_data,
            })
            log("Pushed workspace to Unity", "unity")

        return workspace

    except Exception as e:
        log(f"Detection error: {e}", "error")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/plan")
async def plan_endpoint():
    """
    Generate sorting plan from current workspace using Gemini Agent 2.
    Returns plan to browser AND pushes commands to Unity.
    """
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
        steps = plan.get("sequence", [])
        log(f"Plan ready: {len(steps)} steps")

        if unity_ws and unity_connected:
            asyncio.create_task(_push_plan_to_unity(steps))

        return plan

    except Exception as e:
        log(f"Planning error: {e}", "error")
        return JSONResponse({"error": str(e)}, status_code=500)


async def _push_plan_to_unity(steps):
    """Push sorting commands to Unity over WebSocket."""
    try:
        for step in steps:
            command = {
                "type": "move",
                "step": step["step"],
                "object_id": step["object_id"],
                "target": step["to"],
                "speed": 0.5,
            }
            await unity_ws.send_json(command)
            log(
                f"Unity ← step {step['step']}: "
                f"{step['object_id']} → "
                f"({step['to']['x']:.2f}, {step['to']['y']:.2f})",
                "unity",
            )
            await asyncio.sleep(2)

        await unity_ws.send_json({
            "type": "done",
            "message": f"All {len(steps)} moves complete",
        })
    except Exception as e:
        log(f"Unity push error: {e}", "error")


@app.post("/api/workspace")
async def receive_workspace(request: Request):
    """Direct workspace injection (for testing / feed_photo.py)."""
    global current_workspace
    data = await request.json()

    if "workspace" not in data:
        return JSONResponse({"error": "Missing 'workspace' key"}, status_code=400)

    current_workspace = data
    obj_count = len(data["workspace"].get("objects", []))

    if unity_ws and unity_connected:
        ws_data = data["workspace"].copy()
        for obj in ws_data.get("objects", []):
            if "color" not in obj:
                obj["color"] = category_to_color(obj.get("category", "other"))
        await unity_ws.send_json({"type": "workspace_init", "data": ws_data})

    return {"status": "ok", "objects": obj_count}


@app.get("/api/state")
async def get_state():
    ws = current_workspace.get("workspace", {}) if current_workspace else {}
    objects = ws.get("objects", [])
    return {
        "unity_connected": unity_connected,
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
# WEBSOCKET — Unity connects here (backward compatible)
# ============================================================

@app.websocket("/ws/unity")
async def unity_endpoint(websocket: WebSocket):
    global unity_ws, unity_connected
    await websocket.accept()
    unity_ws = websocket
    unity_connected = True
    log("Unity connected!", "unity")

    if current_workspace:
        ws_data = current_workspace["workspace"].copy()
        for obj in ws_data.get("objects", []):
            if "color" not in obj:
                obj["color"] = category_to_color(obj.get("category", "other"))
        await websocket.send_json({
            "type": "workspace_init",
            "data": ws_data,
        })
        log(f"Sent current workspace to Unity ({len(ws_data.get('objects', []))} objects)")

    try:
        while True:
            msg = await websocket.receive_text()
            data = json.loads(msg)
            log(f"Unity ack: {data.get('status', 'unknown')}", "unity")
    except Exception:
        unity_connected = False
        unity_ws = None
        log("Unity disconnected", "unity")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print("\n" + "=" * 54)
    print("  PHANTOM LIMB — Pipeline Server v3")
    print("  " + "-" * 50)
    print("  Browser demo:  http://localhost:8000")
    print("  Unity WS:      ws://localhost:8000/ws/unity")
    print("  API state:     http://localhost:8000/api/state")
    print("  " + "-" * 50)
    print("  Gemini key:    " + (
        "SET" if os.environ.get("GEMINI_API_KEY") else "NOT SET"
    ))
    print("=" * 54 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)