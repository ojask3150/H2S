"""Unit tests: adapters, analysis pipeline, and service wiring.

Run: python -m pytest tests/ -v
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from aap.analysis import SurgeCurve, build_assessment
from aap.config import Settings
from aap.met_adapters import parse_atcf_deck, parse_nhc_advisory
from aap.models import GeeLayers, MeteorologicalData

from fixtures_nhc import NHC_ADVISORY
from fixtures_payloads import LAYERS_PAYLOAD, MET_PAYLOAD

NOW = datetime(2026, 9, 29, 6, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# SurgeCurve math
# --------------------------------------------------------------------------- #
class TestSurgeCurve:
    def test_feed_curve_interpolates(self):
        curve = SurgeCurve(2.2, {"T-16h": 0.3, "T-14h": 1.5, "T-10h": 1.9, "T-4h": 2.2})
        assert curve.height_at(-14) == pytest.approx(1.5)
        assert curve.height_at(-12) == pytest.approx(1.7)  # midpoint 1.5→1.9
        assert curve.height_at(-20) == pytest.approx(0.3)  # clamped low
        assert curve.height_at(-2) == pytest.approx(2.2)   # clamped high

    def test_default_ramp_when_no_curve(self):
        curve = SurgeCurve(2.2, None)
        assert curve.height_at(-18) == pytest.approx(0.0)
        assert curve.height_at(-4) == pytest.approx(2.2)
        assert curve.height_at(-11) == pytest.approx(1.1)  # midpoint
        assert curve.height_at(-20) == pytest.approx(0.0)
        assert curve.height_at(-2) == pytest.approx(2.2)

    def test_crossing_elevation(self):
        curve = SurgeCurve(2.2, {"T-16h": 0.3, "T-14h": 1.5, "T-10h": 1.9, "T-4h": 2.2})
        # SUB-A at 1.5 m: curve hits exactly at T-14h.
        assert curve.crossing_hours_before(1.5) == pytest.approx(-14.0)
        # Above peak -> never.
        assert curve.crossing_hours_before(3.0) is None

    def test_crossing_scales_with_peak(self):
        curve = SurgeCurve(2.2, {"T-16h": 0.3, "T-14h": 1.5, "T-10h": 1.9, "T-4h": 2.2})
        # With peak scaled to 1.9, elevation 1.5 is reached proportionally earlier.
        cross = curve.crossing_hours_before(1.5, peak_m=1.9)
        assert cross is not None
        assert cross > -14.0  # later (smaller magnitude) than -14h


# --------------------------------------------------------------------------- #
# NHC parser
# --------------------------------------------------------------------------- #
class TestNHCParser:
    def test_parse_current_position_and_winds(self):
        met = parse_nhc_advisory(NHC_ADVISORY, storm_id="al092026")
        assert met.trajectory[0].lat == pytest.approx(23.4)
        assert met.trajectory[0].lon == pytest.approx(-78.2)
        assert met.sustained_wind_kmh >= 85 * 1.852  # at least current winds

    def test_parse_forecast_blocks(self):
        met = parse_nhc_advisory(NHC_ADVISORY, storm_id="al092026")
        assert len(met.trajectory) == 4  # current + 3 forecast points
        fc = met.trajectory[1]
        assert fc.status == "forecast"
        assert fc.lat == pytest.approx(24.2)
        assert fc.lon == pytest.approx(-79.1)
        # Strongest forecast wind (105 kt) carried into advisory value.
        assert met.sustained_wind_kmh == pytest.approx(105 * 1.852)

    def test_missing_issue_time_raises(self):
        with pytest.raises(Exception):
            parse_nhc_advisory("no useful content here", storm_id="x")


# --------------------------------------------------------------------------- #
# ATCF parser
# --------------------------------------------------------------------------- #
ATCF_DECK = ",".join([
    "al", "09", "2026092906", "03", "BEST", "0", "-23.4", "-78.2", "85", "965",
]) + ",,,,,,,,," + "\n" + ",".join([
    "al", "09", "2026093012", "36", "OFCL", "36", "24.2", "-79.1", "95", "950",
]) + ",,,,,,,,,"

ATCF_DECK_SIGNED_LAT = ",".join([
    "io", "01", "2026092906", "0", "BEST", "0", "17.8N", "88.1E", "80", "970",
]) + ",,,,,,,,\n" + ",".join([
    "io", "01", "2026093012", "24", "OFCL", "24", "17.4N", "87.9E", "90", "960",
]) + ",,,,,,,,,"


class TestATCFParser:
    def test_parse_rows_and_taus(self):
        met = parse_atcf_deck(ATCF_DECK, storm_id="al092026", source="test")
        assert len(met.trajectory) == 2
        assert met.trajectory[0].status == "current"
        assert met.trajectory[1].status == "forecast"
        assert met.sustained_wind_kmh == pytest.approx(95 * 1.852)

    def test_parse_hemisphere_tokens(self):
        met = parse_atcf_deck(ATCF_DECK_SIGNED_LAT, storm_id="io012026", source="test")
        assert met.trajectory[0].lat == pytest.approx(17.8)
        assert met.trajectory[0].lon == pytest.approx(88.1)
        assert met.trajectory[1].lat == pytest.approx(17.4)

    def test_garbage_rows_skipped(self):
        deck = "garbage line\n,,,,\n" + ATCF_DECK
        met = parse_atcf_deck(deck, storm_id="x", source="test")
        assert len(met.trajectory) == 2


# --------------------------------------------------------------------------- #
# Analysis pipeline
# --------------------------------------------------------------------------- #
@pytest.fixture
def layers() -> GeeLayers:
    return GeeLayers.model_validate(LAYERS_PAYLOAD)


@pytest.fixture
def met() -> MeteorologicalData:
    return MeteorologicalData.model_validate(MET_PAYLOAD)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        trigger_wind_sustained_kmh=150.0,
        trigger_wind_gust_kmh=200.0,
        trigger_rainfall_mm_48h=350.0,
        trigger_surge_m=2.0,
    )


class TestBuildAssessment:
    def test_four_keys_present(self, layers, met, settings):
        out = build_assessment(layers, met, settings, now=NOW)
        assert out.vulnerability_assessment
        assert out.parametric_triggers
        assert out.evacuation_pathways
        assert out.advisories if hasattr(out, "advisories") else out.automated_advisories

    def test_vulnerability_matches_demo(self, layers, met, settings):
        out = build_assessment(layers, met, settings, now=NOW)
        by_id = {v.asset_id: v for v in out.vulnerability_assessment}
        sub = by_id["SUB-A"]
        assert sub.asset_elevation_m == pytest.approx(1.5)
        assert sub.expected_surge_m == pytest.approx(2.2)
        assert sub.inundation_margin_m == pytest.approx(-0.7)
        assert sub.anticipated_failure_window.startswith("T-14.0h")

    def test_shelter_and_feeder_windows(self, layers, met, settings):
        out = build_assessment(layers, met, settings, now=NOW)
        by_id = {v.asset_id: v for v in out.vulnerability_assessment}
        assert by_id["SHEL-M2"].anticipated_failure_window.startswith("T-14.7h")
        assert by_id["FDR-F2"].anticipated_failure_window.startswith("T-15.8h")
        # Safe shelter has no surge window.
        assert by_id["SHEL-M1"].anticipated_failure_window.startswith("NONE")

    def test_triggers_from_met_values(self, layers, met, settings):
        out = build_assessment(layers, met, settings, now=NOW)
        by_id = {t.trigger_id: t for t in out.parametric_triggers}
        assert by_id["TRG-WIND-SUSTAINED"].status == "MET"       # 165 >= 150
        assert by_id["TRG-WIND-GUST"].status == "NOT_MET"        # 185 < 200
        assert by_id["TRG-RAIN-48H"].status == "MET"             # 420 >= 350
        # Surge 2.2 vs threshold 2.0, but CI lower bound 1.9 -> CONDITIONAL_MET.
        assert by_id["TRG-SURGE-PEAK"].status == "CONDITIONAL_MET"

    def test_triggers_not_evaluable_without_surge(self, layers, met, settings):
        met.storm_surge.peak_height_m = None
        out = build_assessment(layers, met, settings, now=NOW)
        surge_trigger = next(t for t in out.parametric_triggers if t.trigger_id == "TRG-SURGE-PEAK")
        assert surge_trigger.status == "NOT_EVALUABLE"
        # Vulnerability items must state not-evaluable, not fabricate windows.
        assert all(v.anticipated_failure_window.startswith("UNKNOWN") for v in out.vulnerability_assessment)

    def test_evacuation_chronology(self, layers, met, settings):
        out = build_assessment(layers, met, settings, now=NOW)
        risky = [p for p in out.evacuation_pathways if p.designation == "FLOOD_RISK"]
        assert [p.road_id for p in risky] == ["R-5", "R-3", "R-1"]
        assert risky[0].clearance_priority == 1
        safe = {p.road_id for p in out.evacuation_pathways if p.designation == "SAFE_ROUTE"}
        assert safe == {"R-7", "R-9"}

    def test_advisory_priorities_and_refs(self, layers, met, settings):
        out = build_assessment(layers, met, settings, now=NOW)
        advs = out.automated_advisories
        assert [a.priority_rank for a in advs] == list(range(1, len(advs) + 1))
        assert all(a.directive for a in advs)
        assert all(a.linked_assessment_ref for a in advs)

    def test_eta_derived_when_missing(self, layers, met, settings):
        met.eta_landfall_hours = None
        out = build_assessment(layers, met, settings, now=NOW)
        # Last trajectory point is 2026-09-30T00:00Z; now = 09-29T06:00Z -> 18h.
        by_id = {v.asset_id: v for v in out.vulnerability_assessment}
        assert by_id["SUB-A"].anticipated_failure_window.startswith("T-14.0h")


# --------------------------------------------------------------------------- #
# Service wiring
# --------------------------------------------------------------------------- #
def test_output_schema_roundtrip():
    from aap.models import AssessmentOutput
    schema = AssessmentOutput.model_json_schema()
    assert set(schema["properties"]) == {
        "vulnerability_assessment", "parametric_triggers", "evacuation_pathways", "automated_advisories"
    }
