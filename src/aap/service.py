"""FastAPI HTTP service exposing the anticipatory-action assessment.

Endpoints
---------
GET  /health              liveness + config echo (no secrets)
GET  /storms              storm catalog (live-active + historical + demo)
GET  /assessment          fetch met feed + layers, run analysis, return 4-key JSON
POST /assessment/reasoned Gemini multimodal reasoning over GEE map exports + met
GET  /dispatch            assessment -> SMS/email templates + contract triggers
GET  /scene               aggregate payload for the frontend (per storm)
GET  /schema              output JSON schema for downstream consumers
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import catalog
from .analysis import build_assessment
from .config import Settings, get_settings
from .dispatcher import DispatchBundle, build_dispatch
from .gee_client import get_layers
from .gemini_engine import GeminiError, MapExport, reason
from .met_adapters import FeedError, fetch_met
from .models import AssessmentOutput, GeeLayers, MeteorologicalData

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("aap.service")

app = FastAPI(
    title="Anticipatory Action Platform — Coastal Cyclone",
    description=(
        "Pre-landfall anticipatory action engine: intersects live cyclone "
        "meteorology with GEE DEM/infrastructure layers to produce vulnerability "
        "assessments, parametric trigger status, evacuation pathways, and "
        "automated municipal advisories."
    ),
    version="0.1.0",
)

# Local frontend dev origins; tighten via env in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

router = APIRouter()


def _safe_settings(settings: Settings) -> dict:
    return {
        "gee_mode": settings.gee_mode,
        "met_sources": settings.met_source_list,
        "met_poll_interval_s": settings.met_poll_interval_s,
        "triggers": {
            "wind_sustained_kmh": settings.trigger_wind_sustained_kmh,
            "wind_gust_kmh": settings.trigger_wind_gust_kmh,
            "rainfall_mm_48h": settings.trigger_rainfall_mm_48h,
            "surge_m": settings.trigger_surge_m,
        },
        "gee_assets_configured": bool(
            settings.gee_dem_asset and settings.gee_infra_asset and settings.gee_roads_asset
        ),
        "reasoning_engine": settings.reasoning_engine,
        "gemini_model": settings.gemini_model,
        "gemini_configured": bool(settings.gemini_api_key),
    }


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    return {"status": "ok", "time_utc": datetime.now(timezone.utc).isoformat(), "config": _safe_settings(settings)}


# --------------------------------------------------------------------------- #
# Live feed probing + storm resolution
# --------------------------------------------------------------------------- #
LIVE_ID = "live"
_LIVE_PROBE_TIMEOUT_S = 8.0


def _live_sources(settings: Settings) -> list[str]:
    """Configured met sources with the offline 'static' fixture removed."""
    return [s for s in settings.met_source_list if s != "static"]


async def _fetch_live(settings: Settings, mode: str) -> tuple[GeeLayers, MeteorologicalData]:
    """Fetch a genuinely live storm from the non-static configured feeds."""
    sources = _live_sources(settings)
    if not sources:
        raise FeedError("no live met sources configured (MET_SOURCES has only 'static')")
    live_settings = settings.model_copy(update={"met_sources": ",".join(sources)})
    return await _load_met_and_layers(live_settings, mode)


async def _probe_live(settings: Settings, mode: str) -> dict[str, Any]:
    """Best-effort check for an active live storm (never raises)."""
    if not _live_sources(settings):
        return {"active": False, "storm_id": None, "detail": "No live met sources configured."}
    try:
        _, met = await asyncio.wait_for(_fetch_live(settings, mode), timeout=_LIVE_PROBE_TIMEOUT_S)
        return {"active": True, "storm_id": met.storm_id, "detail": f"Live feed: {met.source}"}
    except Exception as exc:  # noqa: BLE001 - probe must never raise
        return {"active": False, "storm_id": None, "detail": f"No active live system ({exc.__class__.__name__})."}


async def _resolve_scene(
    settings: Settings, storm: Optional[str], mode: str
) -> tuple[GeeLayers, MeteorologicalData, dict[str, Any]]:
    """Return (layers, met, meta) for the requested storm.

    storm is a catalog id ('amphan-2020', 'demo', ...), 'live', or None
    (defaults to the offline demo). Historical layers are recentered on the
    storm's landfall so the map stays coherent.
    """
    if storm == LIVE_ID:
        layers, met = await _fetch_live(settings, mode)
        meta = {
            "name": met.storm_id,
            "status": "LIVE",
            "basin": "as reported by feed",
            "source": met.source,
            "summary": "Live meteorological feed — assessed in real time.",
        }
        return layers, met, meta

    storm_id = storm or catalog.DEMO_ID
    try:
        met, meta = catalog.get_storm(storm_id)
    except catalog.StormNotFound as exc:
        raise HTTPException(status_code=404, detail=f"unknown storm id {storm_id!r}") from exc

    base = get_layers("static")
    if meta.get("status") == "DEMO":
        layers = base  # demo district already sits at the demo storm's landfall
    else:
        layers = catalog.recenter_layers(base, meta["landfall_lat"], meta["landfall_lon"])
    return layers, met, meta


@router.get("/storms")
async def storms(
    mode: str | None = Query(default=None, description="Override GEE mode for the live probe"),
) -> dict:
    """Storm catalog for the picker: live status + historical + demo."""
    settings = get_settings()
    live = await _probe_live(settings, mode or settings.gee_mode)
    entries: list[dict] = []
    if live["active"]:
        entries.append(
            {
                "id": LIVE_ID,
                "storm_id": live["storm_id"],
                "name": live["storm_id"],
                "status": "LIVE",
                "basin": "Bay of Bengal",
                "summary": live["detail"],
            }
        )
    entries.extend(catalog.list_storms())
    return {"live": live, "storms": entries}


@router.get("/assessment", response_model=AssessmentOutput)
async def assessment(
    storm: str | None = Query(default=None, description="Catalog storm id, 'live', or blank for demo"),
    mode: str | None = Query(default=None, description="Override GEE mode: 'live' or 'static'"),
) -> JSONResponse:
    settings = get_settings()
    layers, met, _meta = await _resolve_scene(settings, storm, mode or settings.gee_mode)
    output = build_assessment(layers, met, settings)
    return JSONResponse(content=output.model_dump(), status_code=200)


class MapExportIn(BaseModel):
    data_base64: str = Field(description="Base64-encoded GEE map export image")
    mime_type: str = "image/png"
    caption: str = ""


class ReasonedRequest(BaseModel):
    storm: str | None = Field(default=None, description="Catalog storm id, 'live', or blank for demo")
    mode: str | None = Field(default=None, description="Override GEE mode: 'live' or 'static'")
    map_exports: list[MapExportIn] = Field(default_factory=list)


async def _load_met_and_layers(settings: Settings, gee_mode: str):
    if gee_mode not in ("live", "static"):
        raise HTTPException(status_code=400, detail=f"invalid mode; use 'live' or 'static'")
    try:
        layers = get_layers(gee_mode)
    except Exception as exc:
        log.exception("layer load failed")
        raise HTTPException(status_code=502, detail=f"layer ingestion failed: {exc}") from exc
    try:
        async with httpx.AsyncClient() as client:
            met = await fetch_met(settings, client)
    except FeedError as exc:
        log.error("met feed failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"meteorological ingestion failed: {exc}") from exc
    return layers, met


@router.post("/assessment/reasoned")
async def assessment_reasoned(req: ReasonedRequest) -> JSONResponse:
    """Run the Gemini multimodal engine (falls back to the deterministic pipeline)."""
    settings = get_settings()
    layers, met, _meta = await _resolve_scene(settings, req.storm, req.mode or settings.gee_mode)
    exports = [
        MapExport.from_base64(m.data_base64, mime_type=m.mime_type, caption=m.caption)
        for m in req.map_exports
    ]
    try:
        result = reason(settings, met, layers, exports)
    except GeminiError as exc:
        raise HTTPException(status_code=502, detail=f"reasoning engine failed: {exc}") from exc
    return JSONResponse(
        content={
            "engine": result.engine,
            "model": result.model,
            "thinking_level": result.thinking_level,
            "notes": result.notes,
            "storm_id": met.storm_id,
            "assessment": result.output.model_dump(),
        },
        status_code=200,
    )


@router.get("/dispatch", response_model=DispatchBundle)
async def dispatch(
    storm: str | None = Query(default=None, description="Catalog storm id, 'live', or blank for demo"),
    mode: str | None = Query(default=None, description="Override GEE mode: 'live' or 'static'"),
) -> JSONResponse:
    """Assess, then stage SMS/email templates and parametric contract triggers."""
    settings = get_settings()
    layers, met, _meta = await _resolve_scene(settings, storm, mode or settings.gee_mode)
    output = build_assessment(layers, met, settings)
    bundle = build_dispatch(output, storm_ref=met.storm_id)
    return JSONResponse(content=bundle.model_dump(), status_code=200)


@router.get("/scene")
async def scene(
    storm: str | None = Query(default=None, description="Catalog storm id, 'live', or blank for demo"),
    mode: str | None = Query(default=None, description="Override GEE mode: 'live' or 'static'"),
    include_catalog: bool = Query(default=True, description="Embed the storm catalog + live status"),
) -> JSONResponse:
    """One-shot payload for the frontend: layers + trajectory + assessment + dispatch + storm meta."""
    settings = get_settings()
    resolved_mode = mode or settings.gee_mode
    layers, met, meta = await _resolve_scene(settings, storm, resolved_mode)
    output = build_assessment(layers, met, settings)
    bundle = build_dispatch(output, storm_ref=met.storm_id)
    payload: dict[str, Any] = {
        "storm_id": storm or catalog.DEMO_ID,
        "storm_meta": meta,
        "layers": layers.model_dump(),
        "meteorology": met.model_dump(),
        "assessment": output.model_dump(),
        "dispatch": bundle.model_dump(),
    }
    if include_catalog:
        payload["live"] = await _probe_live(settings, resolved_mode)
        payload["catalog"] = catalog.list_storms()
    return JSONResponse(content=payload, status_code=200)


@router.get("/schema")
def schema() -> dict:
    return AssessmentOutput.model_json_schema()


# Expose API endpoints both at root (e.g. /health, /scene) and under /api/* (e.g. /api/health, /api/scene)
app.include_router(router)
app.include_router(router, prefix="/api")

# Static files serving for production static frontend bundle (e.g. compiled Next.js export)
static_dir = os.environ.get("STATIC_DIR") or os.path.join(
    os.path.dirname(__file__), "..", "..", "frontend", "out"
)
static_dir = os.path.abspath(static_dir)

if os.path.exists(static_dir):
    next_assets_dir = os.path.join(static_dir, "_next")
    if os.path.exists(next_assets_dir):
        app.mount("/_next", StaticFiles(directory=next_assets_dir), name="next_assets")

    @app.get("/{full_path:path}")
    async def serve_static_or_spa(full_path: str):
        target_file = os.path.join(static_dir, full_path)
        if full_path and os.path.isfile(target_file):
            return FileResponse(target_file)
        index_file = os.path.join(static_dir, "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        raise HTTPException(status_code=404, detail="Not Found")
