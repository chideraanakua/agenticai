# Phantom Limb

**Adaptive Spatial-Sorting & Safety Engine**
Track 3: Robotics & Simulation

An AI-orchestrated pipeline that turns workspace chaos into collision-free robotic sorting — validated in a digital twin before a single motor moves.

---

## What it does

Point a camera at a messy table. Phantom Limb identifies every object, plans the optimal sorting sequence, enforces human safety zones, and executes the plan in a real-time 3D simulation — all in under 10 seconds.

```
Camera → Agent 1 (Gemini + OpenCV) → Agent 2 (Gemini) → Robot Arm Simulation
         identifies objects              plans sorting      executes pick-and-place
         pixel-perfect coords            avoids humans      Three.js + Unity
```

---

## Architecture

The system uses a hybrid AI approach where each tool does what it's best at:

**Agent 1 — Perception (Gemini + OpenCV hybrid)**
Gemini's vision model sees the full workspace image and identifies objects by name, category, and approximate location. OpenCV then refines each bounding box to pixel-perfect precision using edge detection and contour snapping. Gemini tells you _what_, OpenCV tells you _where_.

**Agent 2 — Planning (Gemini spatial reasoning)**
Takes the workspace JSON with all object positions and safety zones, then outputs an optimal pick-and-place sorting sequence. Groups objects by category, respects no-go zones, minimizes total arm movement. Falls back to a heuristic planner if the API is unavailable.

**Dual-output simulation**
The same sorting plan drives two renderers simultaneously. A Three.js browser simulation serves as the primary demo (one URL, no install for judges), while a Unity digital twin connects via WebSocket for development validation.

---

## Project structure

```
phantom-limb/
├── server.py            # FastAPI backend — serves everything
├── gemini_agents.py         # Agent 1 (hybrid detection) + Agent 2 (planner)
├── static/
│   └── index.html           # Browser demo (camera + annotations + plan + Three.js)
├── PipelineReceiver.cs      # Unity WebSocket client (optional)
├── detect.py                # Standalone Gemini-only detection (CLI)
├── feed_photo.py            # CLI tool to feed photos into the pipeline
└── test_data/
    └── sample_workspace.json
```

---

## Prerequisites

- Python 3.10 or later (tested on 3.12)
- A Gemini API key (free tier is sufficient)
- A webcam or phone camera (or use photo upload)
- Unity 6 LTS with NativeWebSocket package (optional, for digital twin)

---

## Installation

### 1. Clone the repository

```bash
git clone <your-repo-url>
cd VLA
```

### 2. Install Python dependencies

Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install fastapi uvicorn websockets google-generativeai opencv-python numpy Pillow
```

### 3. Set your Gemini API key

Get a free key from https://aistudio.google.com/apikey

```bash
# Linux / macOS
export GEMINI_API_KEY="your-key-here"

# Windows PowerShell
$env:GEMINI_API_KEY="your-key-here"

# Windows CMD
set GEMINI_API_KEY=your-key-here
```

### 4. Verify installation

```bash
python -c "import fastapi, cv2, google.generativeai, numpy, PIL; print('All dependencies OK')"
```

---

## Usage

### Running the browser demo (primary)

```bash
python server.py
```

Open http://localhost:8000 in your browser. You will see a 4-panel interface:

| Panel            | Location     | Purpose                                                         |
| ---------------- | ------------ | --------------------------------------------------------------- |
| Workspace input  | Top left     | Camera feed or photo upload. Click "Scan workspace" to capture. |
| Detected objects | Top right    | Annotated image with bounding boxes, labels, and safety zones.  |
| Sorting plan     | Bottom left  | Step-by-step plan from Agent 2 with reasoning.                  |
| Digital twin     | Bottom right | Three.js 3D simulation with robot arm executing the plan.       |

**Step-by-step demo flow:**

1. Click **Start camera** (or **Upload photo** if no webcam)
2. Point the camera at a table with several objects on it
3. Click **Scan workspace** — Agent 1 runs detection (3–5 seconds)
4. Review detected objects in the top-right panel
5. Click **Generate plan** — Agent 2 creates the sorting sequence (2–3 seconds)
6. Click **Execute in sim** — watch the robot arm sort the objects
7. The arm picks each object, lifts it, swings to the target zone, and places it down while avoiding the red safety zone

### Running with Unity (optional, backward compatible)

The server maintains a WebSocket endpoint at `ws://localhost:8000/ws/unity`. To use it:

1. Start the server: `python server.py`
2. Open your Unity project with `PipelineReceiver.cs` attached to a GameObject
3. Hit Play in Unity — the console should show "Connected to Python server!"
4. Run the browser demo as normal — Unity receives the same workspace and move commands simultaneously

The browser header bar shows Unity's connection status in real time.

### CLI tools (for testing without the browser)

**Feed a photo directly into the pipeline:**

```bash
python feed_photo.py path/to/photo.jpg
```

This runs detection, sends the workspace to the server, triggers the plan, and pushes commands to Unity. The server must be running.

**Run standalone detection (no server needed):**

```bash
# Gemini-only detection
python detect.py photo.png --preview
```

---

## API reference

All endpoints are served by `server.py` on port 8000.

### POST /api/detect

Accepts a base64-encoded image, runs hybrid detection (Gemini + OpenCV), returns workspace JSON.

**Request:**

```json
{
  "image": "<base64-encoded image data>"
}
```

**Response:**

```json
{
  "workspace": {
    "frame_id": "frame_143025",
    "timestamp": "2025-07-15T14:30:25Z",
    "dimensions": { "width_px": 1280, "height_px": 720 },
    "objects": [
      {
        "id": "obj_001",
        "label": "saucepan",
        "category": "kitchen",
        "centroid": { "x": 0.42, "y": 0.22 },
        "bounding_box": {
          "top_left": { "x": 0.3, "y": 0.08 },
          "bottom_right": { "x": 0.55, "y": 0.38 }
        },
        "area_ratio": 0.065,
        "confidence": 0.92,
        "coord_source": "opencv"
      }
    ],
    "safety_zones": [
      {
        "id": "zone_human_01",
        "type": "human_presence",
        "polygon": [
          { "x": 0.68, "y": 0.2 },
          { "x": 1.0, "y": 0.2 },
          { "x": 1.0, "y": 1.0 },
          { "x": 0.68, "y": 1.0 }
        ],
        "risk_level": "high"
      }
    ]
  }
}
```

### POST /api/plan

Generates a sorting plan from the current workspace using Gemini Agent 2.

**Response:**

```json
{
  "strategy": "category_grouping",
  "reasoning": "Group by function into 3 clusters, all targets left of safety zone.",
  "clusters": [
    {
      "cluster_id": "kitchen",
      "label": "Kitchen items",
      "target_zone": { "x": 0.12, "y": 0.15 },
      "members": ["obj_001"]
    }
  ],
  "sequence": [
    {
      "step": 1,
      "action": "pick_and_place",
      "object_id": "obj_001",
      "from": { "x": 0.42, "y": 0.22 },
      "to": { "x": 0.12, "y": 0.15 },
      "reason": "Clear largest item first"
    }
  ]
}
```

### POST /api/workspace

Direct workspace injection for testing. Accepts raw workspace JSON.

### GET /api/state

Returns current server state including Unity connection status, object count, and recent logs.

### WebSocket /ws/unity

Unity connects here. Receives `workspace_init` and `move` commands. Sends acknowledgments back.

---

## How the hybrid detection works

The detection pipeline solves a fundamental problem: vision-language models are great at identification but imprecise at localization, while computer vision is precise at localization but can't identify objects semantically.

```
Full image
    │
    ▼
┌─────────────────────────┐
│  Gemini Vision API      │   "There's a saucepan near the top,
│  (identifies + rough    │    a cookbook in the center,
│   bounding boxes)       │    a human on the right..."
└─────────┬───────────────┘
          │  rough bounding boxes (±10-20%)
          ▼
    For each detected object:
          │
          ▼
┌─────────────────────────┐
│  OpenCV refinement      │   Crop rough region (with 15% padding)
│  • Canny edge detection │   → find contours
│  • Contour snapping     │   → snap to largest contour
│  • Precise bounding box │   → pixel-perfect coordinates
└─────────┬───────────────┘
          │
          ▼
    Merged result: Gemini labels + OpenCV coordinates
```

Each object in the output includes a `coord_source` field indicating whether OpenCV successfully refined the coordinates ("opencv") or the system fell back to Gemini's estimate ("gemini_estimate").

---

## Configuration

### Environment variables

| Variable         | Required | Description                                |
| ---------------- | -------- | ------------------------------------------ |
| `GEMINI_API_KEY` | Yes      | Google AI Studio API key for Gemini access |

### Tunable parameters in gemini_agents.py

| Parameter       | Default            | Description                                                                                                   |
| --------------- | ------------------ | ------------------------------------------------------------------------------------------------------------- |
| `GEMINI_MODEL`  | `gemini-2.5-flash` | Which Gemini model to use. Flash is fast and cheap.                                                           |
| `padding_ratio` | `0.15`             | How much to expand Gemini's rough box when cropping for OpenCV refinement. Increase if OpenCV misses objects. |
| `SAFETY_MARGIN` | `0.05`             | Extra margin around detected humans for safety zones (normalized).                                            |

### Tunable parameters in detect.py (OpenCV-only mode)

| Parameter             | Default    | Description                                                         |
| --------------------- | ---------- | ------------------------------------------------------------------- |
| `MIN_AREA_RATIO`      | `0.008`    | Minimum object area as fraction of image. Increase to filter noise. |
| `MIN_SKIN_AREA_RATIO` | `0.04`     | Minimum skin region area for human detection.                       |
| `COLOR_RANGES`        | (see file) | HSV ranges for each color. Use `--tune` to calibrate.               |

---

## Troubleshooting

### "GEMINI_API_KEY not set"

Make sure you exported the key in the same terminal session where you run the server. The key does not persist across terminal restarts.

```bash
export GEMINI_API_KEY="your-key-here"
python server.py
```

### Camera not working in browser

Browsers require HTTPS for camera access except on `localhost`. If accessing from another device on your network, the camera will be blocked. Either run the demo on the same machine, or set up an HTTPS proxy.

The "Upload photo" button works regardless of camera permissions.

### Objects not detected

Try with a cleaner background. The hybrid detection works best when objects contrast with the table surface. If specific objects are missed, check the Gemini response in the server logs — the issue is usually Gemini not identifying the object rather than OpenCV failing to refine it.

### Unity won't connect

Ensure `server_v3.py` is running before you press Play in Unity. Check that `PipelineReceiver.cs` has `serverUrl` set to `ws://localhost:8000/ws/unity`. Check the Unity console for connection errors.

If using NativeWebSocket v1.x (the `#upm` branch), make sure the `DispatchMessageQueue()` call is present in `Update()`. If using v2.x, remove that call.

### OpenCV import error on Linux

If `cv2` fails to import with a library error:

```bash
sudo apt-get install libgl1-mesa-glx libglib2.0-0
```

Or use the headless version:

```bash
pip install opencv-python-headless
```

### Plan generation falls back to heuristic

This happens when Gemini returns unparseable JSON or times out. Check your API key quota at https://aistudio.google.com. The free tier allows 15 requests per minute — sufficient for demos but can throttle under rapid testing.

---

## Version history

File: server.py

| Version | Changes                                                                          |
| ------- | -------------------------------------------------------------------------------- |
| v1      | Hardcoded workspace, basic WebSocket to Unity                                    |
| v2      | HTTP API endpoints, accepts real detection data, web dashboard                   |
| v3      | Hybrid Gemini+OpenCV detection, dual output (browser + Unity), full browser demo |

---

## Team

**Phantom Limb** — the robot arm that thinks before it moves.

Built for Track 3: Robotics & Simulation.

---

## License

This project was built during a hackathon. See repository for license details.