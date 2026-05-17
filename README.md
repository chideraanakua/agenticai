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
         pixel-perfect coords            avoids humans      Three.js browser sim
```

---

## Architecture

**Agent 1 — Perception (Gemini + OpenCV hybrid)**
Gemini's vision model sees the full workspace image and identifies objects by name, category, and approximate location. OpenCV then refines each bounding box to pixel-perfect precision using edge detection and contour snapping. Gemini tells you _what_, OpenCV tells you _where_.

**Agent 2 — Planning (Gemini spatial reasoning)**
Takes the workspace JSON with all object positions and safety zones, then outputs an optimal pick-and-place sorting sequence. Groups objects by category, respects no-go zones, minimizes total arm movement. Falls back to a heuristic planner if the API is unavailable.

---

## Project structure

```
safearc/
├── server.py            # FastAPI backend — serves everything
├── conftest.py          # pytest path setup
├── requirements.txt
├── core/
│   ├── gemini_agents.py # Agent 1 (hybrid detection) + Agent 2 (planner)
│   └── storage.py       # SQLite session persistence + analytics
├── cli/
│   ├── detect.py        # Standalone detection tool (no server needed)
│   └── feed_photo.py    # Feed a photo into a running server
├── tests/
│   └── test_enforce_safety.py
├── samples/             # Test images
└── static/
    ├── index.html       # 4-panel browser demo (Three.js sim)
    └── dashboard.html   # Session history + rating dashboard
```

---

## Prerequisites

- Python 3.10 or later (tested on 3.12)
- A Gemini API key (free tier is sufficient — get one at https://aistudio.google.com/apikey)
- A webcam or phone camera (or use photo upload)

---

## Installation

### 1. Clone the repository

```bash
git clone <your-repo-url>
cd safearc
```

### 2. Install Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Set your Gemini API key

```bash
# Create a .env file (recommended)
echo 'GEMINI_API_KEY="your-key-here"' > .env

# Or export directly
export GEMINI_API_KEY="your-key-here"
```

### 4. Verify installation

```bash
python -c "import fastapi, cv2, google.genai, numpy, PIL; print('All dependencies OK')"
```

---

## Usage

### Running the demo

```bash
python server.py
```

- **Demo:** http://localhost:8000
- **Dashboard:** http://localhost:8000/dashboard

The 4-panel interface:

| Panel           | Location     | Purpose                                                         |
| --------------- | ------------ | --------------------------------------------------------------- |
| Workspace input | Top left     | Camera feed or photo upload. Click "Scan workspace" to capture. |
| Detected objects| Top right    | Annotated image with bounding boxes, labels, and safety zones.  |
| Sorting plan    | Bottom left  | Step-by-step plan from Agent 2 with reasoning.                  |
| Digital twin    | Bottom right | Three.js 3D simulation with robot arm executing the plan.       |

**Demo flow:**

1. Click **Start camera** (or **Upload photo**)
2. Point at a table with several objects
3. Click **Scan workspace** — Agent 1 runs detection (3–5 s)
4. Click **Generate plan** — Agent 2 creates the sorting sequence (2–3 s)
5. Click **Execute in sim** — watch the robot arm sort the objects

### Accessing from a phone (ngrok)

Browsers only allow camera access over HTTPS or `localhost`. To use the live camera feed from a phone, expose the server via [ngrok](https://ngrok.com):

**1. Install ngrok**

```bash
# macOS
brew install ngrok

# Linux
snap install ngrok
# or download from https://ngrok.com/download
```

**2. Authenticate (one-time, free account)**

```bash
ngrok config add-authtoken <your-token>
# Get your token at https://dashboard.ngrok.com/authtokens
```

**3. Start the server and tunnel**

```bash
# Terminal 1
python server.py

# Terminal 2
ngrok http 8000
```

Ngrok prints a forwarding URL like `https://xxxx.ngrok-free.app`. Open that URL on your phone — the camera will work because ngrok provides a valid HTTPS endpoint.

> The frontend uses relative API paths, so all requests automatically go to the correct backend regardless of which URL you open.

---

### CLI tools

**Standalone detection (no server needed):**

```bash
python cli/detect.py photo.png --preview
```

**Feed a photo into a running server:**

```bash
python cli/feed_photo.py path/to/photo.jpg
```

### Running tests

```bash
PYTHONPATH=. python tests/test_enforce_safety.py
```

---

## API reference

| Endpoint              | Method | Purpose                                              |
| --------------------- | ------ | ---------------------------------------------------- |
| `/api/detect`         | POST   | Run hybrid detection on a base64 image               |
| `/api/plan`           | POST   | Generate sorting plan from current workspace         |
| `/api/workspace`      | POST   | Direct workspace injection (for testing)             |
| `/api/state`          | GET    | Current server state (objects, zones, logs)          |
| `/api/sessions`       | GET    | Session history (paginated, max 100)                 |
| `/api/sessions/{id}`  | GET    | Single session detail                                |
| `/api/feedback/{id}`  | POST   | Submit user rating (1–5) + comment                  |
| `/api/stats`          | GET    | Aggregate analytics                                  |

---

## How the hybrid detection works

```
Full image
    │
    ▼
┌─────────────────────────┐
│  Gemini Vision API      │   "There's a bottle near the top,
│  (identifies + rough    │    a cookbook on the right,
│   bounding boxes)       │    a human hand reaching in..."
└─────────┬───────────────┘
          │  rough bounding boxes (±10–20%)
          ▼
    For each detected object:
          │
          ▼
┌─────────────────────────┐
│  OpenCV refinement      │   Crop rough region (15% padding)
│  • Canny edge detection │   → find contours
│  • Contour snapping     │   → snap to largest contour
│  • Precise bounding box │   → pixel-perfect coordinates
└─────────┬───────────────┘
          │
          ▼
    Merged result: Gemini labels + OpenCV coordinates
```

Each object includes a `coord_source` field — `"opencv"` if refined successfully, `"gemini_estimate"` if OpenCV fell back to Gemini's rough box.

---

## Configuration

### Environment variables

| Variable        | Required | Default             | Description                        |
| --------------- | -------- | ------------------- | ---------------------------------- |
| `GEMINI_API_KEY`| Yes      | —                   | Google AI Studio API key           |
| `GEMINI_MODEL`  | No       | `gemini-2.5-flash-lite`  | Gemini model to use             |

### Tunable parameters (`core/gemini_agents.py`)

| Parameter       | Default | Description                                                        |
| --------------- | ------- | ------------------------------------------------------------------ |
| `padding_ratio` | `0.15`  | Crop expansion for OpenCV refinement. Increase if boxes are missed.|
| `SAFETY_MARGIN` | `0.05`  | Extra buffer around human zones (normalized coords).               |

---

## Troubleshooting

### "GEMINI_API_KEY not set"

Export the key in the same terminal session, or put it in a `.env` file at the project root.

### Camera not working in browser

Browsers require HTTPS for camera access except on `localhost`. If you're accessing from a phone or another device, use ngrok to get a valid HTTPS URL — see [Accessing from a phone](#accessing-from-a-phone-ngrok) above. Otherwise use the **Upload photo** button as a fallback.

### Plan falls back to heuristic

Gemini returned unparseable JSON or timed out. Check your API quota at https://aistudio.google.com — the free tier allows 15 requests/minute.

### OpenCV import error on Linux

```bash
sudo apt-get install libgl1-mesa-glx libglib2.0-0
# or
pip install opencv-python-headless
```

---

## Team

**Phantom Limb** — the robot arm that thinks before it moves.
Built for TechEx Hackathon — Track 3: Robotics & Simulation.
