# CONTEXT.md

This file tracks the current state of SafeArc / Phantom Limb — what's implemented, design decisions made, and what changed over time. For how to run and develop in this repo, see CLAUDE.md.

## What It Is

**Phantom Limb** (repo: `safearc`) is an AI-orchestrated workspace sorting and collision-avoidance system built for TechEx Hackathon Track 3: Robotics & Simulation. Feed any overhead image into the pipeline → AI identifies objects and human-presence zones → planner generates a collision-free pick-and-place sequence → robot arm executes in a browser-based Three.js simulation. Full pipeline runs in under 10 seconds.

The input is not limited to a table scene — the system works on any image (warehouse floor, lab bench, outdoor scene, etc.). The table is just the primary demo.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, FastAPI, Uvicorn |
| AI SDK | `google-genai` (`genai.Client`) — **not** the deprecated `google-generativeai` |
| AI Model | `gemini-2.5-flash-lite` (default; override via `GEMINI_MODEL` env var) |
| Computer Vision | OpenCV 4.13 (CSRT tracking, contour refinement), MediaPipe Pose (human zone tracking), Pillow, NumPy |
| Database | SQLite (`sessions.db`) |
| Frontend | Vanilla JS + Three.js (3D sim) + Chart.js (dashboard) |
| Config | python-dotenv, `.env` for `GEMINI_API_KEY` and `GEMINI_MODEL` |

## Project Structure

```
safearc/
├── server.py                   # FastAPI app — API routes, session tracking, video pipeline
├── tracker.py                  # Frame-level tracking (CSRT objects, MediaPipe human zone)
├── conftest.py                 # pytest sys.path setup
├── requirements.txt
├── .env                        # GEMINI_API_KEY, GEMINI_MODEL (not committed)
├── pose_landmarker_lite.task   # MediaPipe model — auto-downloaded on first run (not committed)
├── core/
│   ├── gemini_agents.py        # Two-agent AI pipeline (detect + plan)
│   └── storage.py              # SQLite session persistence + analytics
├── cli/
│   ├── detect.py               # Standalone CLI detection tool
│   └── feed_photo.py           # CLI tool to push a photo into running server
├── tests/
│   └── test_enforce_safety.py  # Safety enforcement unit tests (5 tests)
├── samples/                    # Test images
└── static/
    ├── index.html              # 4-panel browser demo (Three.js sim + live video tracking)
    └── dashboard.html          # Session review + rating dashboard
```

## Data Flow

### Single-image mode (original)
```
Camera / Upload
  → base64 → POST /api/detect
  → Gemini Vision + OpenCV refinement → workspace JSON
  → workspace stored in-memory + SQLite, annotated canvas displayed

  → POST /api/plan
  → Gemini planner (fallback: heuristic) → safety enforcement pass
  → plan JSON stored, step list displayed

  → "Execute in sim" → Three.js animation loop
  → session saved to SQLite → user rates 1–5 → dashboard
```

### Video tracking mode (new — `video-support` branch)
```
Camera live feed (5fps frame loop)
  → POST /api/video/frame (no Gemini)
      → CSRT tracker updates all object positions
      → MediaPipe Pose updates human safety zone polygon
      → drift check: |current_centroid - anchor| > 0.05 → stale step
      → path check: updated zone intersects carry path → blocked step
      → stale/blocked highlights pushed to step list UI
      → live bounding boxes redrawn on canvas overlay

  → if stale/blocked + cooldown (8s) elapsed:
      → _auto_replan() [background asyncio task]
          → Gemini Agent 1 re-detect
          → CSRT trackers re-initialised
          → Gemini Agent 2 re-plan
          → new plan + workspace pushed via WS /ws/unity
          → frontend rebuilds Three.js scene + auto-executes

  → POST /api/step/complete (frontend ping per animation step)
      → server advances current_step_index
      → completed steps excluded from drift checks
```

## Key Design Decisions

- **Gemini only at boundaries** — Gemini is called at initialisation and on auto-replan triggers only. All inter-frame tracking (CSRT + MediaPipe) runs locally with no API cost.
- **8-second replan cooldown** — prevents quota exhaustion on Gemini free tier (15 req/min); each replan costs 2 requests (detect + plan).
- **Grace window after detection** — `last_replan_time` is set to `time.time()` in `_init_trackers()`, not 0.0, so the first tracking frame cannot trigger an immediate replan.
- **Retry on quota errors** — `_gemini_with_retry()` retries both agents up to 3× with 5→15→30s backoff; heuristic fallback fires if all retries exhaust.
- **Compact planning JSON** — workspace sent to Agent 2 uses `separators=(',',':')` and 2dp coordinates (~35% token reduction vs original).
- **GEMINI_MODEL env var** — defaults to `gemini-2.5-flash-lite`; swap to `gemini-2.0-flash-lite` for higher free-tier rate limits during testing.

## Changelog

### 2026-05-16 — SDK migration, project restructure, sim labels, safe-area fix

- **Refactor: project restructure** — flat root reorganised into `core/` (AI pipeline + storage), `cli/` (standalone tools), `tests/` (unit tests), `samples/` (test images). All imports updated across `server.py`, `cli/feed_photo.py`, and `tests/`. `conftest.py` added at project root for pytest path resolution.

- **Refactor: SDK migration (`core/gemini_agents.py`)** — migrated from deprecated `google-generativeai` to `google-genai`. Client is now `genai.Client(api_key=...)`, calls use `client.models.generate_content(model=..., contents=[...], config=types.GenerateContentConfig(...))`. Both Agent 1 and Agent 2 calls wrapped with `_gemini_with_retry()` from video-support branch. `requirements.txt` updated (`google-generativeai` → `google-genai`).

- **Config: model pinned to `gemini-2.5-flash-lite`** — default in `core/gemini_agents.py`, `.env`, and `README.md`. Previously was `gemini-2.5-flash`.

- **Feat: Three.js step-number labels (`static/index.html`)** — each object block in the 3D sim now shows a circular badge with its plan step number, rendered via `CanvasTexture` sprites. Labels track blocks during animation. Skipped objects get a badge too (step number from plan).

- **Feat: sim hidden until plan generated (`static/index.html`)** — a `#simPlaceholder` overlay covers the Three.js canvas until a sorting plan is available, preventing an empty sim from being shown to users who haven't generated a plan yet.

- **Fix: `_compute_safe_area` too-narrow x range (`core/gemini_agents.py`)** — single-pass zone processing was applying x constraints from top-half zones (e.g. hand zone) to the bottom-half safe area, shrinking it from ~0.72 wide to ~0.20. Fixed with a two-pass approach: Pass 1 computes safe y bounds; Pass 2 applies x constraints only from zones that actually overlap the safe y range. Safe area is now wide enough to accommodate all relocatable objects without stacking.

- **Fix: `_enforce_safety` skip vs relocate semantics** — clarified and tested: `skip=true` means the object's pick-up position (`from`) is inside a safety zone (robot cannot safely reach in to grab it). `relocated=true` means the `from` is safe but the destination or carry path was bad and a safe alternative was found. Objects with a safe `from` and safe destination pass through unchanged.

- **Tests (`tests/test_enforce_safety.py`)** — 5 unit tests covering: object inside zone → skip, object outside zone safe path → unchanged, outside zone path crosses zone → relocate, destination inside zone → relocate, multiple objects inside zone → all skipped. All 5 passing.

### 2026-05-15 — Video tracking pipeline (`video-support` branch)
- **Feat:** `tracker.py` — `ObjectTracker` (OpenCV CSRT per object) and `HumanZoneTracker` (MediaPipe Pose Tasks API) provide frame-level position updates without Gemini calls. MediaPipe model auto-downloaded on first use.
- **Feat:** `POST /api/video/frame` — runs CSRT + MediaPipe each frame, checks drift and path-crossing, triggers `_auto_replan()` when issues found and 8s cooldown elapsed.
- **Feat:** `POST /api/step/complete` — frontend pings per animation step; server advances `current_step_index` so completed steps are excluded from drift checks.
- **Feat:** `WS /ws/unity` — repurposed from dead Unity code into a push-only channel; server pushes new plan + workspace to all connected clients after auto-replan.
- **Feat:** Live bounding box overlay on camera feed — transparent canvas over video, updated each tracking frame with category-coloured boxes and labels.
- **Feat:** Tracking status badge on camera panel — `● tracking` (green) / `⟳ recalculating…` (amber).
- **Feat:** Stale (amber) and blocked (red) step highlighting in plan panel — updated each frame to reflect current tracker state.
- **Feat:** WebSocket replan handler in frontend — receives new plan, rebuilds Three.js scene, auto-executes without user interaction.
- **Fix:** Camera feed was permanently hidden after `scanWorkspace()` snapshot capture. Now restores automatically when detection completes.
- **Fix:** `last_replan_time` was initialised to `0.0` (epoch), causing the first tracking frame to always trigger a spurious replan. Fixed by setting it to `time.time()` in `_init_trackers()`.
- **Fix:** Gemini 429/quota errors were unhandled crashes. `_gemini_with_retry()` now retries both agents up to 3× with 5→15→30s backoff.
- **Fix:** Planning JSON compacted (`separators=(',',':')`, 2dp coords) to reduce token usage ~35% and stay within free-tier limits.

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
- **Bug fix:** `_enforce_safety()` now deduplicates destinations — a candidate placement is rejected if another object was already relocated within 0.08 normalized units of it, preventing objects from being stacked at the same spot.
- **Bug fix:** Safety enforcement (`_enforce_safety()`) was skipped when Gemini succeeded — it only ran on the heuristic fallback path. Fixed so it always runs.
- **Bug fix:** `GEMINI_MODEL` had no default; crashes if env var missing. Now defaults to a pinned model (later changed to `gemini-2.5-flash-lite`).
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
