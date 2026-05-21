# Contributing to SafeArc

Thanks for your interest in improving SafeArc — an adaptive spatial-sorting and
safety engine for robotic pick-and-place. This guide covers how to set up a
development environment, the conventions we follow, and how to get changes
merged.

By participating in this project you agree to abide by our
[Code of Conduct](CODE_OF_CONDUCT.md).

## Getting started

### Prerequisites

- Python 3.10 or later (tested on 3.12)
- A Gemini API key — free tier is enough ([aistudio.google.com/apikey](https://aistudio.google.com/apikey))
- A webcam or phone camera (photo upload also works)

### Set up your environment

```bash
git clone https://github.com/srikeerthis/safearc
cd safearc

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

echo 'GEMINI_API_KEY="your-key-here"' > .env
```

Verify the install:

```bash
python -c "import fastapi, cv2, google.genai, numpy, PIL; print('All dependencies OK')"
```

### Run it locally

```bash
python server.py
```

- Demo: http://localhost:8000
- Dashboard: http://localhost:8000/dashboard

See the [README](README.md) for the full demo flow, CLI tools, and architecture
overview.

## Making changes

### Branching

Branch off `master`. Use a short, descriptive branch name with a type prefix:

```
fix/black-frame-on-scan
feat/multi-camera-support
docs/api-reference
```

### Commit messages

We follow [Conventional Commits](https://www.conventionalcommits.org/). Prefix
each commit with its type:

| Prefix      | Use for                                          |
| ----------- | ------------------------------------------------ |
| `feat:`     | A new feature                                    |
| `fix:`      | A bug fix                                        |
| `docs:`     | Documentation only                               |
| `refactor:` | Code change that neither fixes a bug nor adds a feature |
| `test:`     | Adding or correcting tests                       |
| `chore:`    | Tooling, dependencies, housekeeping              |

Example: `fix: black image on initial scan; allow workspace re-scan`

### Code style

- **Python** — follow PEP 8. Keep functions focused; match the surrounding
  style in `core/`, `server.py`, and `tracker.py`.
- **JavaScript/CSS** — the frontend in `static/` is plain ES modules and CSS,
  no build step. Match the existing formatting in neighbouring files.
- Keep changes minimal and scoped — avoid unrelated reformatting in the same PR.

### Tests

Run the safety-enforcement test suite before submitting:

```bash
PYTHONPATH=. python tests/test_enforce_safety.py
```

If you change planning or safety logic in `core/gemini_agents.py`, add or update
tests under `tests/` to cover it.

## Submitting a pull request

1. Make sure your branch is up to date with `master`.
2. Run the tests and confirm the demo still works (`python server.py`).
3. Push your branch and open a pull request against `master`.
4. In the PR description, explain **what** changed and **why**, and link any
   related issue (e.g. `Closes #12`).
5. Keep each PR focused on a single concern — smaller PRs are reviewed faster.

## Reporting bugs and requesting features

Open an issue on [GitHub](https://github.com/srikeerthis/safearc/issues). For
bugs, include:

- What you expected to happen vs. what actually happened
- Steps to reproduce
- Your OS, Python version, and `GEMINI_MODEL` if relevant
- Relevant server logs or browser console output

## Project structure

A full breakdown lives in the [README](README.md#project-structure). The key
entry points:

| Path                    | Responsibility                                  |
| ----------------------- | ----------------------------------------------- |
| `server.py`             | FastAPI backend, REST API, static file serving  |
| `core/gemini_agents.py` | Agent 1 (detection) + Agent 2 (planner)         |
| `core/storage.py`       | SQLite session persistence and analytics        |
| `tracker.py`            | Frame-level object/human tracking (video mode)  |
| `static/js/`            | Frontend modules — camera, detection, planning, simulation, overlay |

Thanks for contributing!