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

### 2026-05-13
- Full codebase analyzed and documented: FastAPI backend, Gemini + OpenCV hybrid detection, safety-aware planner, Three.js sim, SQLite session tracking, Chart.js dashboard
- Two-agent pipeline fully implemented and working
- **Unity dropped** — Three.js is now the sole simulation layer; `unity_scripts/` and `/ws/unity` WebSocket are dead code (kept for backward compat)
- Dashboard with session review, per-session detail modal, and rating submission implemented
- CONTEXT.md and CLAUDE.md created

*Update this file whenever a meaningful feature lands, a design decision is made, or a known issue is resolved.*
