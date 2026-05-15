# CONTEXT.md

This file tracks the current state of SafeArc / Phantom Limb — what's implemented, design decisions made, and what changed over time. For how to run and develop in this repo, see CLAUDE.md.

## What It Is

**Phantom Limb** (repo: `safearc`) is an AI-orchestrated workspace sorting and collision-avoidance system built for TechEx Hackathon Track 3: Robotics & Simulation. Feed any overhead image into the pipeline → AI identifies objects and human-presence zones → planner generates a collision-free pick-and-place sequence → robot arm executes in a browser-based Three.js simulation. Full pipeline runs in under 10 seconds.

The input is not limited to a table scene — the system works on any image (warehouse floor, lab bench, outdoor scene, etc.). The table is just the primary demo.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, FastAPI, Uvicorn |
| AI Vision | Google Gemini 2.5 Flash (object detection + spatial reasoning) |
| Computer Vision | OpenCV 4.13, Pillow, NumPy |
| Database | SQLite (`sessions.db`) |
| Frontend | Vanilla JS + Three.js (3D sim) + Chart.js (dashboard) |
| Config | python-dotenv, `.env` for `GEMINI_API_KEY` |

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

## Data Flow

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

## Changelog

### 2026-05-14 — Session persistence, image storage & replay

- **Feat (`gemini_agents.py`):** Plan steps now include `object_label` alongside `object_id` — injected after `_enforce_safety()` runs using an `id → label` map built from the workspace objects. Applies to both Gemini and heuristic plan paths.

- **Fix (`static/dashboard.html`):** Session modal was rendering raw `obj_00x` IDs in plan steps. Now uses `step.object_label` with fallback to `step.object_id` for sessions recorded before this change.

- **Feat: image storage (`storage.py`, `server.py`):** After each detect call, `original.jpg` and `annotated.jpg` are saved to `static/sessions/{id}/`. Annotation is drawn server-side with Pillow — safety zone polygons as semi-transparent red overlays, bounding boxes coloured by `category_to_color()` from `gemini_agents.py`. Image URLs stored in two new SQLite columns (`image_original`, `image_annotated`). `init_db()` auto-migrates existing DBs via `PRAGMA table_info`. FastAPI serves session images via a `/static` `StaticFiles` mount.

- **Fix (image save):** Both `bounding_box` (stored as `{top_left: {x,y}, bottom_right: {x,y}}`) and polygon points (stored as `{x, y}` dicts) were being accessed as flat lists — crashing the annotated image generation silently. Fixed with `isinstance(bb, dict)` and `isinstance(p, dict)` guards. Image save is wrapped in try/except so a failure never blocks the detect response.

- **Feat: session replay (`static/index.html`):** Main portal now supports `/?session=<id>`. On load, `maybeRestoreSession()` fetches the stored workspace + plan from `GET /api/sessions/{id}`, fetches `image_original` as a blob and converts to data URL (matching the live detect flow), calls `drawAnnotations`, `updateSimWorkspace`, and `renderSteps`, then enables "Execute in sim". A session banner appears below the header showing the session ID and a "+ New Session" button that navigates to `/`. Grid height is adjusted dynamically by `banner.offsetHeight` so the plan panel bottom bar stays visible.

- **Feat: Load & Simulate button (`static/dashboard.html`):** Session detail modal footer shows a "Load & Simulate" button that navigates to `/?session=<id>`. Button is only shown when a plan exists (`plan.sequence.length > 0`); plan-less sessions show a hint instead.

- **Fixes (session restore, multiple):**
  - `btnExec` was hardcoded `disabled` in HTML and never enabled during restore — fixed.
  - `planEmpty` is a child of `stepsList`; `renderSteps` wipes it via `innerHTML = ""`, so the subsequent `getElementById("planEmpty").style.display` call threw null. Line removed.
  - `renderTagStrip` called inside `drawAnnotations` could throw if `tagStrip` was null in the restore context — added null guard (`if (!strip) return`).
  - `drawAnnotations` and its image fetch are now isolated in their own `try/catch` so annotation failure never blocks sim/steps from loading.
  - `updateSimWorkspace` and `renderSteps` now run before the image fetch so the simulation is ready even if image loading is slow or fails.
  - Banner is shown before the try block so failure messages are always visible to the user.

### 2026-05-13 — Pre-demo fixes
- **Bug fix:** Safety enforcement (`_enforce_safety()`) was skipped when Gemini succeeded — it only ran on the heuristic fallback path. Fixed so it always runs.
- **Bug fix:** `GEMINI_MODEL` had no default; crashes if env var missing. Now defaults to `gemini-2.5-flash`.
- **Fix:** Confidence scores were hardcoded to 0.92/0.78. Now derived from OpenCV `coverage` ratio — varies per object.
- **Fix:** Detection prompt was scoped to "table/desk". Generalized to any workspace scene, movable objects only.
- **Fix:** `alert()` popups on errors replaced with inline red status text in each panel.
- **Feat:** Live server log polling during Gemini calls — `/api/state` polled every 800ms, streamed into UI status elements.

### 2026-05-13 — Initial state
- Two-agent pipeline fully implemented: Gemini + OpenCV hybrid detection, Gemini spatial planner with heuristic fallback, safety enforcement
- **Unity dropped** — Three.js is now the sole simulation layer; `unity_scripts/` and `/ws/unity` WebSocket are dead code (kept for backward compat)
- Dashboard with session review, per-session detail modal, and rating submission implemented
- CONTEXT.md and CLAUDE.md created

*Update this file whenever a meaningful feature lands, a design decision is made, or a known issue is resolved.*
