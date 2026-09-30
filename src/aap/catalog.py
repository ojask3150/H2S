"""Storm catalog: historical Bay of Bengal cyclones + the offline demo storm.

The catalog turns each entry into a pre-landfall :class:`MeteorologicalData`
snapshot that the existing analysis pipeline can assess, plus display metadata
(name, year, category, landfall place, post-event impact figures).

Historical storm tracks make landfall at different points along the coast, so
:func:`recenter_layers` translates the reference infrastructure district to sit
at the selected storm's landfall — keeping the map coherent for every storm
without a bespoke fixture per district.
"""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from .models import GeeLayers, MeteorologicalData

log = logging.getLogger(__name__)

_HIST_PATH = Path(__file__).resolve().parent / "fixtures" / "historical_storms.json"
_DEMO_PATH = Path(__file__).resolve().parent / "fixtures" / "met_static.json"

DEMO_ID = "demo"


class StormNotFound(KeyError):
    """Requested storm id is not in the catalog."""


@lru_cache
def _raw() -> dict[str, dict]:
    """Map of storm id -> {"meta": {...}, "met": {...}} for every catalog entry."""
    entries: dict[str, dict] = {}

    hist = json.loads(_HIST_PATH.read_text(encoding="utf-8"))
    for s in hist.get("storms", []):
        entries[s["id"]] = {"meta": dict(s["meta"]), "met": s["met"]}

    # The bundled offline demo storm, surfaced through the same catalog.
    demo_met = json.loads(_DEMO_PATH.read_text(encoding="utf-8"))
    entries[DEMO_ID] = {
        "meta": {
            "name": "Synthetic BoB demo",
            "year": 2026,
            "category": "Very Severe Cyclonic Storm (synthetic)",
            "basin": "Bay of Bengal",
            "status": "DEMO",
            "landfall_place": "Reference coastal district (17.4N, 87.9E)",
            "landfall_date": "2026-09-30",
            "peak_wind_kmh": demo_met.get("sustained_wind_kmh"),
            "deaths": None,
            "damage_usd_bn": None,
            "source": "bundled static fixture",
            "summary": "Fully offline synthetic storm used for demos and CI. Runs with no network or credentials (GEE_MODE=static, MET_SOURCES=static).",
        },
        "met": demo_met,
    }
    return entries


def _landfall_latlon(met: MeteorologicalData) -> tuple[float, float]:
    """(lat, lon) of the storm's landfall — the last trajectory point."""
    last = met.trajectory[-1]
    return last.lat, last.lon


def get_storm(storm_id: str) -> tuple[MeteorologicalData, dict[str, Any]]:
    """Return (meteorology, meta) for a catalog storm id."""
    entry = _raw().get(storm_id)
    if entry is None:
        raise StormNotFound(storm_id)
    met = MeteorologicalData.model_validate(entry["met"])
    meta = dict(entry["meta"])
    lat, lon = _landfall_latlon(met)
    meta.setdefault("landfall_lat", round(lat, 3))
    meta.setdefault("landfall_lon", round(lon, 3))
    return met, meta


def summary(storm_id: str) -> dict[str, Any]:
    """Compact catalog-list entry for the picker (meta + a couple met fields)."""
    met, meta = get_storm(storm_id)
    return {
        "id": storm_id,
        "storm_id": met.storm_id,
        **meta,
        "sustained_wind_kmh": met.sustained_wind_kmh,
        "surge_peak_m": met.storm_surge.peak_height_m,
        "eta_landfall_hours": met.eta_landfall_hours,
    }


def list_storms() -> list[dict[str, Any]]:
    """All catalog storms: demo first, then historical newest-first."""
    ids = list(_raw().keys())
    hist = sorted(
        (i for i in ids if i != DEMO_ID),
        key=lambda i: _raw()[i]["meta"].get("year", 0),
        reverse=True,
    )
    ordered = [DEMO_ID, *hist]
    return [summary(i) for i in ordered]


def _geom_centroid(layers: GeeLayers) -> Optional[tuple[float, float]]:
    """Mean (lon, lat) over every point/vertex in the layer geometries."""
    lons: list[float] = []
    lats: list[float] = []

    def _add(geom: Optional[dict]) -> None:
        if not geom:
            return
        coords = geom.get("coordinates")
        if geom.get("type") == "Point" and coords:
            lons.append(coords[0])
            lats.append(coords[1])
        elif geom.get("type") == "LineString" and coords:
            for lon, lat in coords:
                lons.append(lon)
                lats.append(lat)

    for a in layers.infrastructure:
        _add(a.geom)
    for r in layers.arterial_roads:
        _add(r.geom)
    if not lons:
        return None
    return sum(lons) / len(lons), sum(lats) / len(lats)


def recenter_layers(layers: GeeLayers, target_lat: float, target_lon: float) -> GeeLayers:
    """Translate every layer geometry so the district centroid sits at the target.

    DEM elevations are unchanged — the reference district's terrain profile is
    reused; only its map position moves to the storm's landfall so the track and
    infrastructure render together.
    """
    centroid = _geom_centroid(layers)
    if centroid is None:
        return layers
    c_lon, c_lat = centroid
    d_lon = target_lon - c_lon
    d_lat = target_lat - c_lat

    out = layers.model_copy(deep=True)

    def _shift(geom: Optional[dict]) -> Optional[dict]:
        if not geom:
            return geom
        g = deepcopy(geom)
        coords = g.get("coordinates")
        if g.get("type") == "Point" and coords:
            g["coordinates"] = [coords[0] + d_lon, coords[1] + d_lat]
        elif g.get("type") == "LineString" and coords:
            g["coordinates"] = [[lon + d_lon, lat + d_lat] for lon, lat in coords]
        return g

    for a in out.infrastructure:
        a.geom = _shift(a.geom)
    for r in out.arterial_roads:
        r.geom = _shift(r.geom)
    return out
