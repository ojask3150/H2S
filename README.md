# Anticipatory Action Platform — Bay of Bengal

AI-powered predictive risk and vulnerability modeling for coastal cyclones,
built for **pre-landfall anticipatory action**: evacuation routing, grid/shelter
directives, and parametric-insurance trigger evaluation.

The platform intersects live cyclone meteorology with Google Earth Engine
DEM/infrastructure layers, reasons over the scene with **Gemini 3.7 Flash**
(multimodal), and emits four auditable outputs: a vulnerability assessment,
parametric triggers, evacuation pathways, and automated municipal advisories.

## Architecture

```
Met feeds (NHC / JTWC / custom / static) ─┐
                                          ├─► analysis pipeline ─► 4-key assessment
GEE layers (live DEM sampling / static) ──┘          │
                                                     ├─► Gemini 3.7 Flash (multimodal, thinking_level=high)
                                                     └─► dispatcher (SMS / email / parametric contracts)
                                                                     │
                                          Next.js + Leaflet dashboard ◄┘  (/scene)
```

| Layer | Path |
|-------|------|
| Config | `src/aap/config.py` |
| Data models | `src/aap/models.py` |
| GEE ingestion (+ static fallback) | `src/aap/gee_client.py` |
| Met adapters (NHC/JTWC/custom/static) | `src/aap/met_adapters.py` |
| Analysis / surge intersect | `src/aap/analysis.py` |
| Gemini multimodal engine | `src/aap/gemini_engine.py` |
| Advisory dispatcher | `src/aap/dispatcher.py` |
| FastAPI service | `src/aap/service.py` |
| Frontend (Next.js) | `frontend/` |

## Backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows PowerShell, use the native Python environment instead:

```powershell
python -m venv .venv-windows
.\.venv-windows\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Run fully offline (bundled Bay of Bengal demo storm + synthetic DEM layers):

```bash
cp .env.example .env   # optional; defaults work
GEE_MODE=static MET_SOURCES=static PYTHONPATH=src \
  uvicorn aap.service:app --reload --port 8000
```

PowerShell:

```powershell
Copy-Item .env.example .env  # optional; defaults work
$env:GEE_MODE = "static"
$env:MET_SOURCES = "static"
$env:PYTHONPATH = "src"
uvicorn aap.service:app --reload --port 8000
```

Tests:

```bash
PYTHONPATH=src:tests python -m pytest tests/ -q
```

PowerShell:

```powershell
$env:PYTHONPATH = "src;tests"
python -m pytest tests/ -q
```

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness + config echo (no secrets) |
| GET | `/storms` | Storm catalog: live-active (if any) + historical Bay of Bengal cyclones + offline demo, with live-feed status |
| GET | `/assessment` | The 4-key assessment JSON (`?storm=<id>`) |
| POST | `/assessment/reasoned` | Gemini multimodal reasoning over GEE map exports + met |
| GET | `/dispatch` | SMS/email templates + parametric contract triggers (`?storm=<id>`) |
| GET | `/scene` | Aggregate payload for the frontend (layers + met + assessment + dispatch + storm meta + catalog) (`?storm=<id>`) |
| GET | `/schema` | Output JSON schema |

Every assessment endpoint takes an optional `?storm=<id>`:

- `live` — assess a genuinely live storm from the non-static `MET_SOURCES`
  (NHC/JTWC/custom). `/storms` reports whether a live system is active.
- a historical id (`amphan-2020`, `fani-2019`, `yaas-2021`, `mocha-2023`,
  `sidr-2007`, `nargis-2008`, `phailin-2013`, `odisha-1999`) — reconstructs the
  pre-landfall (~T-18h) assessment for that cyclone from curated IMD/JTWC best
  tracks. The reference infrastructure district is re-centered on each storm's
  landfall so the map stays coherent.
- omitted / `demo` — the bundled fully-offline synthetic storm.

### Reasoning engine

`REASONING_ENGINE` selects the engine:

- `auto` (default) — Gemini when `GEMINI_API_KEY` is set, else the deterministic
  analysis pipeline.
- `gemini` — require Gemini (`gemini-3.7-flash`, `thinking_level=high`; legacy
  `temperature`/`top_p` are never sent).
- `deterministic` — always the local pipeline.

`POST /assessment/reasoned` accepts base64 GEE map exports:

```json
{ "map_exports": [{ "data_base64": "<png bytes>", "mime_type": "image/png", "caption": "DEM + infra" }] }
```

### Live mode

Set `GEE_MODE=live` with `GEE_SERVICE_ACCOUNT_JSON` and the `GEE_*_ASSET` ids to
sample real DEM elevations, and `MET_SOURCES=nhc,jtwc,custom` for live feeds.
NHC/JTWC text products publish no numeric surge, so surge-dependent triggers
report `NOT_EVALUABLE` unless a `custom` feed supplies it.

## Frontend

```bash
cd frontend
npm install
npm run dev   # http://localhost:3000, proxies /api/* -> http://localhost:8000
```

A dark geospatial dashboard (Leaflet) plots the storm track, infrastructure
(colored by surge risk), and arterial roads (flood-risk vs. safe inland routes),
alongside panels for all four assessment outputs and the dispatch bundle.

## Deploy on Render

The repository includes a production multi-stage `Dockerfile` and `render.yaml` configured for **Render Free Tier**:

1. `h2s-app` — Unified single-container web service serving both FastAPI backend APIs and static Next.js Leaflet dashboard.

### Option A: Deploy via Render Blueprint (Recommended)
1. Push this repository to GitHub/GitLab.
2. In Render Dashboard, choose **New > Blueprint**, connect this repository, and apply the Blueprint.
3. Render will build the Docker container and deploy the app on the Free Tier automatically.

### Option B: Deploy via Render Web Service
1. Choose **New > Web Service** in Render.
2. Select **Docker** as the Runtime.
3. Point to `Dockerfile` in the root directory.

No API keys are required for default offline demo deployment.

### Local Docker Testing
Build and run locally with Docker:

```bash
docker build -t h2s-app .
docker run -p 8000:8000 h2s-app
```
Access dashboard at `http://localhost:8000/` and health check at `http://localhost:8000/health`.

For live feeds, set environment variables (`GEMINI_API_KEY`, `GEE_MODE=live`, `GEE_SERVICE_ACCOUNT_JSON`, etc.) in Render Dashboard environment settings after deployment.

## Safety note

The dispatcher **stages** SMS/email and smart-contract payloads (`STAGED` /
`READY_TO_EXECUTE`); it does not transmit messages or broadcast transactions.
Wiring an actual gateway/chain is a deliberate, credential-gated integration.
