"""
server_v2.py — Pipeline server with real input support.

Replaces the original hardcoded server.py.
Now accepts real detection data from detect.py via HTTP POST,
and generates a basic sorting plan (Person C replaces this with Gemini later).

Run:     python server_v2.py
Dashboard: http://localhost:8000
Unity WS:  ws://localhost:8000/ws/unity

Install: pip install fastapi uvicorn websockets
"""

import asyncio
import json
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

app = FastAPI(title="Phantom Limb Pipeline")


# ============================================================
# STATE (in-memory, resets on restart)
# ============================================================

# Default hardcoded workspace (used until real data arrives)
current_workspace = {
    "workspace": {
        "frame_id": "hardcoded_default",
        "objects": [
            {"id": "obj_001", "label": "saucepan",  "color": "blue",   "category": "kitchen", "centroid": {"x": 0.42, "y": 0.22}},
            {"id": "obj_002", "label": "cookbook",   "color": "red",    "category": "reading", "centroid": {"x": 0.50, "y": 0.55}},
            {"id": "obj_003", "label": "glasses",   "color": "orange", "category": "reading", "centroid": {"x": 0.25, "y": 0.55}},
            {"id": "obj_004", "label": "pen",       "color": "pink",   "category": "writing", "centroid": {"x": 0.47, "y": 0.80}},
        ],
        "safety_zones": [
            {"id": "zone_human_01", "type": "human_presence", "polygon": [
                {"x": 0.68, "y": 0.20}, {"x": 1.0, "y": 0.20},
                {"x": 1.0, "y": 1.0},   {"x": 0.68, "y": 1.0}
            ]}
        ]
    }
}

current_plan = None
unity_ws = None
connected = False
event_log = []


def log(msg, level="info"):
    event_log.append({"msg": msg, "level": level})
    if len(event_log) > 100:
        event_log.pop(0)
    print(f"  [{level.upper()}] {msg}")


# ============================================================
# API ENDPOINTS
# ============================================================

@app.post("/api/workspace")
async def receive_workspace(request: Request):
    """
    Receive workspace JSON from detect.py (or any vision source).
    Pushes the new workspace to Unity if connected.
    """
    global current_workspace
    data = await request.json()

    if "workspace" not in data:
        return JSONResponse({"error": "Missing 'workspace' key"}, status_code=400)

    current_workspace = data
    obj_count = len(data["workspace"].get("objects", []))
    zone_count = len(data["workspace"].get("safety_zones", []))
    log(f"Received workspace: {obj_count} objects, {zone_count} safety zones")

    # Push to Unity if connected
    if unity_ws and connected:
        # Add color field for Unity rendering if missing
        for obj in data["workspace"]["objects"]:
            if "color" not in obj:
                obj["color"] = category_to_color(obj.get("category", "other"))

        await unity_ws.send_json({
            "type": "workspace_init",
            "data": data["workspace"]
        })
        log("Pushed workspace to Unity", "sent")

    return {"status": "ok", "objects": obj_count, "safety_zones": zone_count}


@app.post("/api/plan")
async def generate_plan():
    """
    Generate a sorting plan from the current workspace.
    
    RIGHT NOW: Uses a basic heuristic (group by category, sort left-to-right).
    PERSON C: Replace generate_basic_plan() with your Gemini function.
    """
    global current_plan
    ws = current_workspace.get("workspace", {})
    objects = ws.get("objects", [])
    safety_zones = ws.get("safety_zones", [])

    if not objects:
        return JSONResponse({"error": "No workspace loaded"}, status_code=400)

    # ===== REPLACE THIS WITH GEMINI =====
    plan = generate_basic_plan(objects, safety_zones)
    # ====================================

    current_plan = plan
    log(f"Generated plan: {len(plan['sequence'])} steps")

    # Push to Unity
    if unity_ws and connected:
        for step in plan["sequence"]:
            command = {
                "type": "move",
                "step": step["step"],
                "object_id": step["object_id"],
                "target": step["to"],
                "speed": 0.5,
            }
            await unity_ws.send_json(command)
            log(f"Step {step['step']}: {step['object_id']} → ({step['to']['x']:.2f}, {step['to']['y']:.2f})", "sent")
            await asyncio.sleep(2)

        await unity_ws.send_json({"type": "done", "message": f"All {len(plan['sequence'])} moves complete"})

    return plan


def generate_basic_plan(objects, safety_zones):
    """
    Basic heuristic sorting — Person C replaces this with Gemini.
    Groups objects by category and assigns target zones on the left side.
    """
    # Find safe x boundary (left of any safety zone)
    safe_x_max = 0.60
    for zone in safety_zones:
        for pt in zone.get("polygon", []):
            safe_x_max = min(safe_x_max, pt["x"] - 0.08)

    # Group by category
    categories = {}
    for obj in objects:
        cat = obj.get("category", "other")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(obj)

    # Assign target zones vertically spaced
    sequence = []
    step_num = 1
    y_slot = 0.12

    for cat_name, cat_objects in categories.items():
        target_x = 0.10
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
                "reason": f"Group {cat_name} items together",
            })
            target_x += 0.12
            step_num += 1
        y_slot += 0.25

    return {
        "strategy": "basic_category_grouping",
        "reasoning": f"Grouped {len(categories)} categories. All targets left of x={safe_x_max:.2f} (safety boundary).",
        "sequence": sequence,
        "plan": {
            "sequence": sequence,  # nested for compat with both formats
        }
    }


def category_to_color(category):
    """Map category to a display color for Unity."""
    return {
        "kitchen": "blue",
        "reading": "green",
        "writing": "pink",
        "other": "orange",
    }.get(category, "gray")


# ============================================================
# WEBSOCKET (Unity connects here)
# ============================================================

@app.websocket("/ws/unity")
async def unity_endpoint(websocket: WebSocket):
    global unity_ws, connected
    await websocket.accept()
    unity_ws = websocket
    connected = True
    log("Unity connected!", "success")

    # Send current workspace
    ws_data = current_workspace.get("workspace", {})
    for obj in ws_data.get("objects", []):
        if "color" not in obj:
            obj["color"] = category_to_color(obj.get("category", "other"))

    await websocket.send_json({
        "type": "workspace_init",
        "data": ws_data
    })
    log(f"Sent workspace ({len(ws_data.get('objects', []))} objects)")

    try:
        while True:
            msg = await websocket.receive_text()
            data = json.loads(msg)
            log(f"Unity ack: {data.get('status', 'unknown')}")
    except Exception:
        connected = False
        unity_ws = None
        log("Unity disconnected", "warn")


# ============================================================
# DASHBOARD
# ============================================================

@app.get("/")
async def dashboard():
    return HTMLResponse("""
    <html><head><title>Phantom Limb — Pipeline</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: system-ui, -apple-system, sans-serif; background: #0f1117; color: #e0e0e0; padding: 24px; }
        h1 { font-size: 22px; font-weight: 600; margin-bottom: 4px; }
        .subtitle { color: #888; font-size: 13px; margin-bottom: 24px; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
        .card { background: #1a1b23; border-radius: 10px; padding: 16px; border: 1px solid #2a2b35; }
        .card h2 { font-size: 14px; font-weight: 500; color: #999; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
        .stat { font-size: 32px; font-weight: 600; }
        .stat.green { color: #5dca7b; }
        .stat.yellow { color: #f0c95d; }
        .stat.red { color: #e05555; }
        .stat.blue { color: #5d9eca; }
        button { padding: 12px 28px; font-size: 14px; font-weight: 500; border-radius: 8px; border: none; cursor: pointer;
                 background: #378ADD; color: white; transition: all 0.2s; }
        button:hover { background: #2a6fc0; transform: translateY(-1px); }
        button:disabled { background: #333; cursor: not-allowed; transform: none; }
        .actions { margin-bottom: 16px; display: flex; gap: 10px; align-items: center; }
        .actions .hint { color: #666; font-size: 12px; }
        #log { background: #12131a; border-radius: 10px; padding: 16px; font-family: 'SF Mono', 'Fira Code', monospace;
               font-size: 12px; min-height: 200px; max-height: 400px; overflow-y: auto; line-height: 1.8;
               border: 1px solid #2a2b35; }
        .log-info { color: #7ba4d4; }
        .log-sent { color: #5dca7b; }
        .log-warn { color: #f0997b; }
        .log-success { color: #5dca7b; font-weight: 500; }
        .obj-list { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
        .obj-tag { font-size: 11px; padding: 3px 8px; border-radius: 4px; background: #2a2b35; color: #ccc; }
    </style></head>
    <body>
        <h1>👻 Phantom Limb</h1>
        <p class="subtitle">Adaptive Spatial-Sorting & Safety Engine</p>
        
        <div class="grid">
            <div class="card">
                <h2>Unity Connection</h2>
                <div id="conn-status" class="stat red">Disconnected</div>
            </div>
            <div class="card">
                <h2>Workspace</h2>
                <div id="obj-count" class="stat blue">0 objects</div>
                <div id="obj-list" class="obj-list"></div>
            </div>
        </div>
        
        <div class="actions">
            <button id="btn-plan" onclick="runPlan()">Generate & Execute Plan</button>
            <span class="hint">Sends sorting commands to Unity</span>
        </div>
        
        <div id="log"><span class="log-info">Dashboard ready. Waiting for connections...</span></div>
        
        <script>
            const log = document.getElementById('log');
            function addLog(msg, cls='info') {
                log.innerHTML += '\\n<span class="log-'+cls+'">[' + new Date().toLocaleTimeString() + '] ' + msg + '</span>';
                log.scrollTop = log.scrollHeight;
            }
            
            async function runPlan() {
                const btn = document.getElementById('btn-plan');
                btn.disabled = true;
                btn.textContent = 'Planning...';
                addLog('Requesting sorting plan...', 'info');
                try {
                    const res = await fetch('/api/plan', { method: 'POST' });
                    const data = await res.json();
                    if (data.error) {
                        addLog('Error: ' + data.error, 'warn');
                    } else {
                        const steps = data.sequence || [];
                        addLog('Plan generated: ' + steps.length + ' steps', 'success');
                        steps.forEach(s => addLog('  Step ' + s.step + ': ' + s.object_id + ' → (' + s.to.x.toFixed(2) + ', ' + s.to.y.toFixed(2) + ')', 'sent'));
                    }
                } catch(e) { addLog('Error: ' + e.message, 'warn'); }
                btn.disabled = false;
                btn.textContent = 'Generate & Execute Plan';
            }
            
            async function poll() {
                try {
                    const res = await fetch('/api/state');
                    const data = await res.json();
                    
                    const connEl = document.getElementById('conn-status');
                    connEl.textContent = data.connected ? 'Connected' : 'Disconnected';
                    connEl.className = 'stat ' + (data.connected ? 'green' : 'red');
                    
                    document.getElementById('obj-count').textContent = data.object_count + ' objects';
                    
                    const listEl = document.getElementById('obj-list');
                    listEl.innerHTML = data.objects.map(o => 
                        '<span class="obj-tag">' + o.label + '</span>'
                    ).join('');
                } catch(e) {}
            }
            setInterval(poll, 1500);
            poll();
        </script>
    </body></html>
    """)


@app.get("/api/state")
async def get_state():
    ws = current_workspace.get("workspace", {})
    objects = ws.get("objects", [])
    return {
        "connected": connected,
        "object_count": len(objects),
        "objects": [{"id": o["id"], "label": o.get("label", "unknown")} for o in objects],
        "safety_zone_count": len(ws.get("safety_zones", [])),
        "has_plan": current_plan is not None,
    }


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print("\n" + "="*50)
    print("  👻 PHANTOM LIMB — Pipeline Server")
    print("  Dashboard:  http://localhost:8000")
    print("  Unity WS:   ws://localhost:8000/ws/unity")
    print("  " + "-"*44)
    print("  Feed a photo:")
    print("    python feed_photo.py photo.jpg")
    print("="*50 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)