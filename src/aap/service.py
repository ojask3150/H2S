"""FastAPI HTTP service exposing the anticipatory-action assessment.

Endpoints
---------
GET /health      liveness + config echo (no secrets)
GET /assessment  fetch met feed + layers, run analysis, return the 4-key JSON
GET /schema      return the output JSON schema for downstream consumers
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from .analysis import build_assessment
from .config import Settings, get_settings
from .gee_client import get_layers
from .met_adapters import FeedError, fetch_met
from .models import AssessmentOutput

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
    }


@app.get("/health")
def health() -> dict:
    settings = get_settings()
    return {"status": "ok", "time_utc": datetime.now(timezone.utc).isoformat(), "config": _safe_settings(settings)}


@app.get("/assessment", response_model=AssessmentOutput)
def assessment(
    mode: str | None = Query(default=None, description="Override GEE mode: 'live' or 'static'"),
) -> JSONResponse:
    settings = get_settings()
    gee_mode = mode or settings.gee_mode
    if gee_mode not in ("live", "static"):
        raise HTTPException(status_code=400, detail=f"invalid mode {mode!r}; use 'live' or 'static'")

    try:
        layers = get_layers(gee_mode)
    except Exception as exc:  # GEE auth/network failures must not 500 raw
        log.exception("layer load failed")
        raise HTTPException(status_code=502, detail=f"layer ingestion failed: {exc}") from exc

    try:
        with httpx.Client() as client:
            met = fetch_met(settings, client)
    except FeedError as exc:
        log.error("met feed failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"meteorological ingestion failed: {exc}") from exc

    output = build_assessment(layers, met, settings)
    return JSONResponse(content=output.model_dump(), status_code=200)


@app.get("/schema")
def schema() -> dict:
    return AssessmentOutput.model_json_schema()
