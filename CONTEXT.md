# SafeArc / Phantom Limb / Adaptive Spatial-Sorting & Safety Engine — Project Context

> Auto-updated as we build. Last updated: 2026-05-13

---

## What It Is

**Phantom Limb** (also: *Adaptive Spatial-Sorting & Safety Engine*, repo: `safearc`) is an AI-orchestrated workspace sorting and collision avoidance system built for **TechEx Hackathon Track 3: Robotics & Simulation**.

The core idea: feed any overhead image into the pipeline → AI identifies objects and human-presence zones → a planner generates a collision-free pick-and-place sequence → the robot arm executes it in a browser-based 3D simulation (Three.js). Full pipeline runs in under 10 seconds.

> **Note:** The input is not limited to a "messy table" — the system works on any image (warehouse floor, lab bench, outdoor scene, etc.). The table scenario is just the primary demo example.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+, FastAPI, Uvicorn |
| AI Vision | Google Gemini 2.5 Flash (object detection + spatial reasoning) |
| Computer Vision | OpenCV 4.13, Pillow, NumPy |
| Database | SQLite (`sessions.db`) |
| Frontend | Vanilla JS + Three.js (3D sim) + Chart.js (dashboard) |
| Config | python-dotenv, `.env` for `GEMINI_API_KEY` |

---

## Project Structure

```
safearc/
├── server.py            # FastAPI app — API routes, session tracking
├── gemini_agents.py     # Two-agent AI pipeline (detect + plan)
├── storage.py           # SQLite session persistence + analytics
├── detect.py            # Standalone CLI detection tool
├── feed_photo.py        # CLI tool to push a photo into running server
├── requirements.txt
├── .env                 # GEMINI_API_KEY (not committed)
└── static/
    ├── index.html       # 4-panel browser demo (Three.js sim)
    └── dashboard.html   # Session review + rating dashboard
```

---

## Architecture

### Two-Agent AI Pipeline

**Agent 1 — Perception (Hybrid Detection)**
1. Gemini Vision API: identifies objects, produces rough bounding boxes
2. OpenCV: crops each rough region, runs Canny edge + contour detection for pixel-precise boxes
3. Outputs `workspace` JSON: objects with `centroid`, `bounding_box`, `confidence`, `coord_source`; plus `safety_zones` for human-presence areas

**Agent 2 — Sorting Planner**
1. Gemini spatial reasoning: groups objects by category, plans optimal pick-and-place sequence
2. Safety enforcement (hard constraints): destination must be outside all safety zones; carry path (straight line) cannot cross any zone polygon
3. Fallback: heuristic grid-based planner if Gemini times out or fails

### Data Flow

```
Camera / Upload
  → base64 → POST /api/detect
  → Gemini Vision + OpenCV refinement
  → workspace JSON stored in-memory + SQLite
  → annotated canvas displayed

  → POST /api/plan
  → Gemini planner (fallback: heuristic)
  → safety enforcement pass
  → plan JSON stored
  → step list displayed

  → "Execute in sim"
  → Three.js animation loop
  → session saved to SQLite
  → user rates 1–5 → stored in DB → dashboard
```

---

## API Endpoints

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/` | Browser demo (index.html) |
| GET | `/dashboard` | Session review dashboard |
| POST | `/api/detect` | Hybrid detection on base64 image |
| POST | `/api/plan` | Generate sorting plan |
| POST | `/api/workspace` | Direct workspace injection (testing) |
| GET | `/api/state` | Current server state (objects, zones, logs) |
| GET | `/api/sessions` | List sessions (paginated, max 100) |
| GET | `/api/sessions/{id}` | Single session detail |
| POST | `/api/feedback/{id}` | Submit rating (1–5) + comment |
| GET | `/api/stats` | Aggregate analytics |
| WS | `/ws/unity` | WebSocket endpoint (legacy — Unity removed) |

---

## Key Data Structures

**Workspace object entry:**
```json
{
  "id": "obj_001",
  "label": "saucepan",
  "category": "kitchen",
  "centroid": {"x": 0.42, "y": 0.22},
  "bounding_box": {
    "top_left": {"x": 0.3, "y": 0.08},
    "bottom_right": {"x": 0.55, "y": 0.38}
  },
  "confidence": 0.92,
  "coord_source": "opencv"
}
```

**Safety zone entry:**
```json
{
  "id": "zone_human_01",
  "type": "human_presence",
  "polygon": [{"x": 0.68, "y": 0.2}, ...],
  "risk_level": "high"
}
```

**Plan step entry:**
```json
{
  "step": 1,
  "action": "pick_and_place",
  "object_id": "obj_001",
  "from": {"x": 0.42, "y": 0.22},
  "to": {"x": 0.12, "y": 0.15},
  "skip": false
}
```

---

## Safety Implementation

- **Path check:** `_path_crosses_any_zone()` — 2D segment vs polygon intersection
- **Point check:** `_point_in_polygon()` — ray-casting algorithm
- **Zone buffer:** 5% margin (`SAFETY_MARGIN = 0.05`) around human zones
- **Skip logic:** if no safe destination exists, step is marked `skip: true` and displayed distinctly in UI

---

## Running Locally

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. Set API key
export GEMINI_API_KEY="your-key"

# 3. Start server
python server.py
# → http://localhost:8000       (demo)
# → http://localhost:8000/dashboard  (analytics)

# CLI tools
python detect.py photo.png --preview
python feed_photo.py photo.jpg
```

---

## Known Limitations

- Camera requires HTTPS except on localhost (browser security policy)
- Gemini free tier: 15 req/min — rapid testing can hit rate limits
- Detection less reliable on cluttered/busy backgrounds
- Heuristic fallback planner is less spatially optimal than Gemini planner
- CORS is wide open (`allow_origins=["*"]`) — fine for local demo, not for prod

---

## Changelog

### 2026-05-13 — Initial context document created
- Analyzed full codebase: FastAPI backend, Gemini + OpenCV hybrid detection, safety-aware planner, Three.js sim, SQLite session tracking, Chart.js dashboard
- Two-agent pipeline fully implemented and working
- **Unity dropped** — Three.js is now the sole simulation/visualization layer; `unity_scripts/` directory and `/ws/unity` WebSocket are dead code
- Dashboard with session review, per-session detail modal, and rating submission implemented
- Clarified scope: input is any image (any scene), not just a messy table — table is demo example only

---

*Update this file whenever a meaningful feature lands, a design decision is made, or a known issue is resolved.*
