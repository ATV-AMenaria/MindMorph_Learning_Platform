## Environment

ALWAYS use the `mindmorph` conda environment for every command in this project — running
Python, pip installs, pytest, uvicorn, anything. Never use base.
- Run one-off commands with `conda run -n mindmorph <cmd>` (e.g. `conda run -n mindmorph python -m pytest -q`).
- Install deps into it: `conda run -n mindmorph pip install <pkg>` (then add to `requirements.txt`).

## App surface

The product is the **Next.js app in `web/`** (TanStack Query → FastAPI in `api/`). Run it with
`cd web && npm run dev` plus `conda run -n mindmorph uvicorn api.main:app --port 8000`. The legacy
Streamlit prototype (`app.py`) was **retired** in P3 #12 — the learning logic lives in `services/`.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- ALWAYS read graphify-out/GRAPH_REPORT.md before reading any source files, running grep/glob searches, or answering codebase questions. The graph is your primary map of the codebase.
- IF graphify-out/wiki/index.md EXISTS, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
    