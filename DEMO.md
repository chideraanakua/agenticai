# Phantom Limb — Demo Walkthrough

Step-by-step guide for running a live demo using sample images.

---

## Setup (one time)

```bash
python server.py
```

- Simulator: http://localhost:8000
- Dashboard: http://localhost:8000/dashboard

Make sure `GEMINI_API_KEY` is set in `.env` before starting.

---

## The 4 panels (quick orientation)

| Panel | Purpose |
|---|---|
| **Top left** — Workspace Input | Upload a photo or use the camera |
| **Top right** — Detected Objects | Annotated image with bounding boxes + safety zones |
| **Bottom left** — Sorting Plan | Step-by-step plan with AI Review card |
| **Bottom right** — Digital Twin | Three.js 3D robot arm simulation |

---

## Recommended demo sequence

### Run 1 — Clean scene (`perspective_3.png`)

Good baseline: 7 objects, 1 safety zone, clear clustering.

1. Click **Upload photo** → select `samples/perspective_3.png`
2. Click **Scan workspace** — wait ~3–5 s for detection
   - Point out: bounding boxes, colour-coded by category, safety zone overlay
3. Click **Generate plan** — wait ~2–3 s
   - Point out: step list with reasons, ⚠ relocated badges
   - Point out: **AI Review card** — predicted score, one-line critique, suggestions
4. Click **Execute in sim** — watch the robot arm sort
   - Point out: step-number badges on each block, categories grouping together

---

### Run 2 — Complex scene (`more_objects.png`)

14 objects across 5 categories — stress test for clustering.

1. Upload `samples/more_objects.png` → Scan → Plan → Simulate
2. Point out: 4 tight same-category columns (electronics, beverages, other, reading)
3. Point out: only 2 skips despite 14 objects — safety enforcement at work

---

### Run 3 — Human in scene (`human_in_middle.jpeg`)

Shows safety zone detection and skip semantics.

1. Upload `samples/human_in_middle.jpeg` → Scan → Plan → Simulate
2. Point out: large red human-presence safety polygon detected automatically
3. Point out: skipped steps — objects whose carry path crosses the human zone are never picked up
4. Explain: *"The robot won't reach over a person. Those objects stay put until the human moves."*

---

## Rating a session (dashboard)

After any run:

1. Open http://localhost:8000/dashboard
2. Click stars (1–5) on the latest session row
3. Click the comment field to add a note
4. Explain: *"Every rating feeds back into the next plan as a few-shot example — the system learns what good looks like from human feedback."*

---

## Showing the learning loop (dashboard)

With several rated sessions in the DB:

1. Open the dashboard
2. Point out the **Rating Trend** chart — improvement over sessions
3. Scroll to **Evaluator Calibration** card
   - MAE: average star error between AI prediction and your rating
   - Bias: negative = evaluator is strict (sets a high bar)
   - Per-session delta table: green = AI was too harsh, red = too generous
4. Explain: *"The evaluator predicts the score before you rate it. The calibration card shows how accurate those predictions are over time."*

---

## Key talking points for judges

- **No retraining** — few-shot learning via prompt injection; improves with every rated session
- **Two feedback signals per example** — user comment + AI critique both injected into next plan
- **Similarity-ranked examples** — most scene-relevant past sessions surface first, not just newest
- **Auto-evaluated sessions count** — even unrated sessions contribute via the evaluator score (COALESCE)
- **Safety is hard-constraint, not a suggestion** — `_enforce_safety()` runs after Gemini, overrides any unsafe destination
- **Evaluator is conservative by design** — measured -0.50 bias; only validates truly strong plans

---

## Sample images reference

| File | Objects | Notable |
|---|---|---|
| `perspective_1.png` | 8 | Many categories, higher relocation count |
| `perspective_2.png` | 5 | Clean beverage cluster, 1 relocation |
| `perspective_3.png` | 7 | Best all-round result, tight clusters |
| `perspective_4.png` | 7 | Challenging geometry, 3 skips |
| `perspective_5.png` | 7 | Good electronics + reading clusters, 2 skips |
| `human_in_middle.jpeg` | 8 | Large human zone, shows skip semantics |
| `more_objects.png` | 14 | Maximum stress test, 4-column clustering |
