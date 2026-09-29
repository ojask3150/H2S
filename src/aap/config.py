"""Service configuration via environment variables (see .env.example)."""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # GEE
    gee_service_account_json: str = ""
    gee_mode: str = "static"  # "live" | "static"
    gee_project: str = ""
    gee_dem_asset: str = ""
    gee_infra_asset: str = ""
    gee_roads_asset: str = ""
    gee_dem_resolution_m: float = 30.0

    # Met feeds: ordered list of sources to try: nhc, jtwc, custom
    met_sources: str = "nhc,jtwc,custom"
    met_nhc_basin: str = "al"
    met_nhc_storm_id: str = ""
    met_jtwc_basin: str = "io"
    met_jtwc_storm_id: str = ""
    met_jtwc_url: str = ""  # optional explicit deck URL, overrides default pattern
    met_custom_url: str = ""
    met_poll_interval_s: int = 900

    # Parametric trigger thresholds (production defaults; tune per policy)
    trigger_wind_sustained_kmh: float = 150.0
    trigger_wind_gust_kmh: float = 200.0
    trigger_rainfall_mm_48h: float = 350.0
    trigger_surge_m: float = 2.0

    log_level: str = "INFO"

    @property
    def met_source_list(self) -> list[str]:
        return [s.strip().lower() for s in self.met_sources.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    # Respect an explicit env override of GEE_MODE even if a .env file disagrees.
    if "GEE_MODE" in os.environ:
        return Settings(gee_mode=os.environ["GEE_MODE"])
    return Settings()
