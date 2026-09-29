"""Synthetic met payload and GEE layer dicts shared across tests."""
from __future__ import annotations

MET_PAYLOAD = {
    "storm_id": "SYNTH-DEMO-01",
    "source": "test",
    "trajectory": [
        {"lat": 17.80, "lon": 88.10, "timestamp_utc": "2026-09-29T06:00:00Z", "status": "current"},
        {"lat": 17.62, "lon": 88.02, "timestamp_utc": "2026-09-29T12:00:00Z", "status": "forecast"},
        {"lat": 17.40, "lon": 87.94, "timestamp_utc": "2026-09-30T00:00:00Z", "status": "landfall_synthetic"},
    ],
    "eta_landfall_hours": 18.0,
    "sustained_wind_kmh": 165,
    "wind_gust_kmh": 185,
    "predicted_rainfall_mm_48h": 420,
    "storm_surge": {
        "peak_height_m": 2.2,
        "confidence_interval_m": [1.9, 2.6],
        "surge_curve_central": {"T-16h": 0.3, "T-14h": 1.5, "T-10h": 1.9, "T-4h": 2.2, "T+2h": 1.8},
    },
}

LAYERS_PAYLOAD = {
    "dem": {"id": "SYNTH-DEM-10m", "vertical_datum": "MSL", "resolution_m": 10},
    "mode": "static",
    "infrastructure": [
        {"id": "SUB-A", "type": "substation", "elevation_m": 1.5, "serves": ["SECTOR-A"]},
        {"id": "FDR-F2", "type": "power_feeder", "elevation_m": 0.4, "serves": ["SECTOR-A"]},
        {"id": "SHEL-M1", "type": "medical_shelter", "elevation_m": 3.8, "capacity_beds": 450},
        {"id": "SHEL-M2", "type": "medical_shelter", "elevation_m": 1.1, "capacity_beds": 120},
    ],
    "arterial_roads": [
        {"id": "R-5", "type": "causeway", "dem_elevation_m": 0.4},
        {"id": "R-3", "type": "coastal_arterial", "dem_elevation_m": 0.8},
        {"id": "R-1", "type": "primary_arterial", "dem_elevation_m": 2.5},
        {"id": "R-7", "type": "inland_arterial", "dem_elevation_m": 6.2},
        {"id": "R-9", "type": "inland_arterial", "dem_elevation_m": 5.8},
    ],
}
