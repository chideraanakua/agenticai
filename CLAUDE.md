# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**SafeArc / Phantom Limb** — An adaptive spatial-sorting and safety engine for a TechEx Hackathon (Track 3: Robotics & Simulation). It uses a two-agent Gemini pipeline for object detection and pick-and-place planning, with a browser-based 3D simulation frontend.

## Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Set required env vars (or put in .env)
export GEMINI_API_KEY="your-key"             # Required — get free key at aistudio.google.com/apikey
export GEMINI_MODEL="gemini-2.5-flash-lite"  # Optional — this is the default

# Start server
python server.py
# Demo: http://localhost:8000
# Dashboard: http://localhost:8000/dashboard
```

No build step — the frontend is a single static HTML file served by FastAPI.

## CLI Tools

```bash
# Standalone detection (no server needed)
python cli/detect.py photo.png --preview

# Feed a photo into a running server
python cli/feed_photo.py photo.jpg
```

## Architecture

### Two-Agent AI Pipeline (`core/gemini_agents.py`)

**Agent 1 — Hybrid Detection:**
1. Gemini Vision API identifies objects and produces rough bounding boxes (±10–20% accuracy)
2. OpenCV crops each rough region and applies Canny edge detection + contour snapping for pixel-precise boxes
3. Human zones get special treatment via skin-tone detection (`_refine_human_zone()`) and safety polygon generation (`_make_safety_polygon()`)

**Agent 2 — Sorting Planner:**
1. Past sessions with ratings or eval scores are fetched from SQLite and similarity-ranked against the current scene via `_score_similarity()` (category Jaccard 60%, object count 30%, zone count 10%)
2. `_build_few_shot_context()` formats the top matches as compact examples (good/bad/mediocre) injected into the prompt — good plans show what to replicate, bad plans + user comment + AI critique show what to avoid
3. Gemini takes the workspace JSON + few-shot context and generates an optimal pick-and-place sequence grouped by category (temperature=0.4 when examples present, 0.0 otherwise)
4. `_enforce_safety()` runs a hard-constraint pass: destinations must be outside safety zones, carry paths must not cross them. Category-aware relocation sorts candidates by proximity to same-category placements already made
5. `_heuristic_plan()` is the fallback if Gemini times out

**Evaluator Agent:**
After planning, `evaluate_plan()` makes a separate Gemini call that critiques the plan against past rated examples and returns a predicted score (1–5), a one-sentence critique, and 2–3 suggestions. Result is stored to the session DB and displayed as an "AI Review" card in the UI.

Safety geometry uses ray-casting for point-in-polygon (`_point_in_polygon()`), 2D segment intersection for path checking (`_path_crosses_any_zone()`), and a `SAFETY_MARGIN = 0.05` buffer around human zones. Steps with no safe destination are marked `skip: true`.

### API Layer (`server.py`)

FastAPI serves both the static frontend and the REST API. Key endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /api/detect` | Runs hybrid detection on a base64 image |
| `POST /api/plan` | Generates sorting plan from current workspace |
| `GET /api/state` | Returns current server state (objects, zones, logs) |
| `GET /api/sessions` | Lists all sessions (paginated, max 100) |
| `POST /api/feedback/{id}` | Submits user rating (1–5) + comment |
| `POST /api/evaluate` | Runs evaluator agent on current plan; stores + returns predicted score/critique |
| `GET /api/calibration` | Returns per-session predicted vs actual deltas + MAE + bias |
| `GET /api/stats` | Aggregate analytics |
| `WS /ws/unity` | Legacy WebSocket — kept for backward compat, not actively used |

### Session Persistence (`core/storage.py`)

SQLite (`sessions.db`) stores sessions with: workspace JSON, plan JSON, object/zone/step counts, user ratings, and evaluator results (`eval_score`, `eval_critique`, `eval_suggestions`). `init_db()` is called at server startup and auto-migrates existing DBs via `PRAGMA table_info`.

### Frontend (`static/index.html`, `static/dashboard.html`)

`index.html` is a 4-panel browser demo: workspace input → annotated canvas → step-by-step plan → Three.js 3D robot arm simulation. All backend communication is `fetch`-based.

`dashboard.html` shows session history, per-session detail modals, a rating interface, a Chart.js rating trend graph, and an evaluator calibration card (MAE, bias, per-session delta table with tooltips).

## Key Data Shapes

```json
// Workspace object
{"id": "...", "label": "cup", "category": "container", "centroid": [x, y],
 "bounding_box": [x1, y1, x2, y2], "confidence": 0.92, "coord_source": "hybrid"}

// Safety zone
{"id": "...", "type": "human_presence", "polygon": [[x,y],...], "risk_level": "high"}

// Plan step
{"step": 1, "action": "pick_and_place", "object_id": "...", "from": [x,y], "to": [x,y], "skip": false}
```

## Tunable Parameters (`core/gemini_agents.py`)

- `padding_ratio = 0.15` — how much to expand crop regions for OpenCV refinement
- `SAFETY_MARGIN = 0.05` — extra buffer fraction around human zones
- Robot base exclusion zone is hard-coded at x: 0.05–0.31, y: 0.0–0.26 (normalized coords)
- Gemini free tier limits vary by model — detection + planning together count as 2 requests

## Known Constraints

- Camera requires HTTPS except on localhost (browser security policy)
- CORS is wide open (`allow_origins=["*"]`) — fine for local demo only
- `sessions.db` and `.env` are gitignored
- SDK: uses `google-genai` (not the deprecated `google-generativeai`); client is `genai.Client`
