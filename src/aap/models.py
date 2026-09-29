"""Pydantic models for the anticipatory-action pipeline.

Three input schemas (GEE layers, meteorological JSON) and one output schema
(the four mandated keys). All timestamps are UTC ISO-8601.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# --------------------------------------------------------------------------- #
# Input: meteorological JSON
# --------------------------------------------------------------------------- #
class TrackPoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    timestamp_utc: str
    status: Optional[Literal["current", "forecast", "landfall", "landfall_synthetic", "historical"]] = None


class StormSurge(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # None when the source feed does not publish numeric surge (NHC/JTWC text
    # products do not). Surge-dependent triggers then evaluate as NOT_EVALUABLE
    # unless the custom feed supplies a value.
    peak_height_m: Optional[float] = Field(default=None, ge=0)
    confidence_interval_m: Optional[list[float]] = Field(default=None, min_length=2, max_length=2)
    surge_curve_central: Optional[dict[str, float]] = None  # e.g. {"T-14h": 1.5}
    qualitative: Optional[str] = None


class MeteorologicalData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    storm_id: str
    source: str = "unknown"
    trajectory: list[TrackPoint] = Field(min_length=1)
    # None when the feed does not state a landfall ETA; the pipeline then
    # anchors T-minus timings to the last trajectory point.
    eta_landfall_hours: Optional[float] = Field(default=None, ge=0)
    sustained_wind_kmh: float = Field(ge=0)
    wind_gust_kmh: Optional[float] = Field(default=None, ge=0)
    predicted_rainfall_mm_48h: Optional[float] = Field(default=None, ge=0)
    storm_surge: StormSurge


# --------------------------------------------------------------------------- #
# Input: GEE-derived layers
# --------------------------------------------------------------------------- #
class DemMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    vertical_datum: str = "MSL"
    resolution_m: Optional[float] = None


class InfrastructureAsset(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    type: Literal["substation", "power_feeder", "medical_shelter", "other"]
    elevation_m: Optional[float] = None  # None when sampled from the DEM at runtime
    serves: Optional[list[str]] = None
    capacity_beds: Optional[int] = None
    path_crossings: Optional[list[str]] = None
    geom: Optional[dict[str, Any]] = None  # GeoJSON geometry for live DEM sampling


class ArterialRoad(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    type: Optional[str] = None
    dem_elevation_m: Optional[float] = None  # None when sampled from the DEM at runtime
    geom: Optional[dict[str, Any]] = None


class GeeLayers(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dem: DemMetadata
    infrastructure: list[InfrastructureAsset] = Field(default_factory=list)
    arterial_roads: list[ArterialRoad] = Field(default_factory=list)
    mode: Literal["live", "static"] = "static"


# --------------------------------------------------------------------------- #
# Output: the four mandated keys
# --------------------------------------------------------------------------- #
class VulnerabilityItem(BaseModel):
    asset_id: str
    asset_type: str
    asset_elevation_m: Optional[float]
    expected_surge_m: float
    inundation_margin_m: Optional[float]
    failure_mode: str
    anticipated_failure_window: str
    basis: str


class TriggerStatus(BaseModel):
    trigger_id: str
    predefined_threshold: str
    observed_value: Optional[float]
    status: Literal["MET", "NOT_MET", "CONDITIONAL_MET", "NOT_EVALUABLE"]
    liquidity_action: str
    verification_source: str


class EvacuationPathway(BaseModel):
    road_id: str
    dem_elevation_m: Optional[float]
    projected_first_flood_time: Optional[str]
    flood_depth_m: float
    designation: Literal["FLOOD_RISK", "SAFE_ROUTE"]
    clearance_priority: Optional[int]
    note: str


class Advisory(BaseModel):
    priority_rank: int
    recipient: str
    lead_time_hours: float
    directive: str
    linked_assessment_ref: str


class AssessmentOutput(BaseModel):
    vulnerability_assessment: list[VulnerabilityItem]
    parametric_triggers: list[TriggerStatus]
    evacuation_pathways: list[EvacuationPathway]
    automated_advisories: list[Advisory]
