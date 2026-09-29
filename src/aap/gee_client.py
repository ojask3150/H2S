"""Google Earth Engine ingestion with a static fallback layer.

Live mode authenticates with a service-account JSON key
(GEE_SERVICE_ACCOUNT_JSON), loads a DEM image plus infrastructure/road feature
collections, and samples DEM elevation at asset geometries via reduceRegion.
Static mode serves the bundled synthetic fixture so the service runs without
credentials.

Live-mode assets (configure via env):
  GEE_DEM_ASSET    Earth Engine elevation image in meters (e.g. MERITDEM,
                   Copernicus DEM GLO-30, or a fused coastal DEM), MSL datum.
  GEE_INFRA_ASSET  FeatureCollection with properties: id, type
                   (substation|power_feeder|medical_shelter|other), optionally
                   serves, capacity_beds.
  GEE_ROADS_ASSET  FeatureCollection with properties: id, optionally type.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from .config import Settings, get_settings
from .models import ArterialRoad, DemMetadata, GeeLayers, InfrastructureAsset

log = logging.getLogger(__name__)

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "gee_static_layer.json"


def load_static_layers() -> GeeLayers:
    """Load the bundled synthetic fixture (same geometry as the demo scenario)."""
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return GeeLayers.model_validate(data)


def load_live_layers(settings: Settings) -> GeeLayers:
    """Authenticate to GEE and sample DEM elevations at asset geometries."""
    import ee  # lazy: static mode must not require an initialized session
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(
        settings.gee_service_account_json, scopes=ee.oauth.SCOPES
    )
    ee.Initialize(credentials=credentials)
    log.info("GEE session initialized (project=%s)", settings.gee_project)

    dem = ee.Image(settings.gee_dem_asset)
    scale = settings.gee_dem_resolution_m

    def _sample(feature: "ee.Feature") -> "ee.Feature":  # type: ignore[name-defined]
        elev = dem.reduceRegion(
            ee.Reducer.first(), feature.geometry(), scale, bestEffort=True
        ).get("elevation")
        return feature.set({"dem_elevation_m": elev})

    infra_features = ee.FeatureCollection(settings.gee_infra_asset)
    infra_features = infra_features.map(_sample).getInfo()["features"]
    road_features = (
        ee.FeatureCollection(settings.gee_roads_asset).map(_sample).getInfo()["features"]
    )

    infrastructure: list[InfrastructureAsset] = []
    for f in infra_features:
        props = f.get("properties", {}) or {}
        infrastructure.append(
            InfrastructureAsset(
                id=str(props.get("id") or f.get("id")),
                type=props.get("type", "other"),
                elevation_m=props.get("dem_elevation_m"),
                serves=props.get("serves"),
                capacity_beds=props.get("capacity_beds"),
                geom=f.get("geometry"),
            )
        )

    arterial_roads: list[ArterialRoad] = []
    for f in road_features:
        props = f.get("properties", {}) or {}
        arterial_roads.append(
            ArterialRoad(
                id=str(props.get("id") or f.get("id")),
                type=props.get("type"),
                dem_elevation_m=props.get("dem_elevation_m"),
                geom=f.get("geometry"),
            )
        )

    return GeeLayers(
        dem=DemMetadata(
            id=settings.gee_dem_asset,
            vertical_datum="MSL",
            resolution_m=scale,
        ),
        infrastructure=infrastructure,
        arterial_roads=arterial_roads,
        mode="live",
    )


@lru_cache
def get_layers(mode: str) -> GeeLayers:
    """Entry point used by the service. mode is 'live' or 'static'."""
    settings = get_settings()
    if mode == "live":
        if not settings.gee_service_account_json:
            raise RuntimeError("GEE_MODE=live requires GEE_SERVICE_ACCOUNT_JSON")
        log.info("loading live GEE layers")
        return load_live_layers(settings)
    log.info("loading static fixture layers")
    return load_static_layers()
