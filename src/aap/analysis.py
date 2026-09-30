"""Analysis pipeline: intersects storm meteorology with DEM-referenced layers.

Produces the four mandated output keys. All surge timing derives from either
an explicit surge curve in the feed (``storm_surge.surge_curve_central``,
keys like ``T-14h`` = hours before landfall) or, absent a curve, a documented
default rising limb: 0 m at T-18h rising linearly to the peak at T-4h.

If the feed does not state ``eta_landfall_hours``, the pipeline anchors
landfall to the last trajectory point and computes ETA from current time.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from .config import Settings
from .models import (
    Advisory,
    AssessmentOutput,
    EvacuationPathway,
    GeeLayers,
    InfrastructureAsset,
    MeteorologicalData,
    TriggerStatus,
    VulnerabilityItem,
)

log = logging.getLogger(__name__)

# Documented default rising limb (hours relative to landfall).
_DEFAULT_RAMP_START_H = -18.0
_DEFAULT_RAMP_PEAK_H = -4.0

# Pluvial proxy: fraction of forecast rainfall that becomes standing water,
# and the ponding depth at which an arterial becomes impassable.
_RUNOFF_COEFF = 0.3
_PLUVIAL_IMPASSABLE_M = 0.10

_CURVE_KEY = re.compile(r"^T([+-]?\d+(?:\.\d+)?)h?$", re.I)


class SurgeCurve:
    """Rising-limb model for surge height vs. hours-before-landfall."""

    def __init__(self, peak_m: float, curve: Optional[dict[str, float]]):
        self.peak_m = peak_m
        points: list[tuple[float, float]] = []
        if curve:
            for key, val in curve.items():
                m = _CURVE_KEY.match(key.strip())
                if m and val >= 0:
                    points.append((-abs(float(m.group(1))), float(val)))  # store as hours-before-landfall
            points.sort()
        self.points = points  # [(hours_before_landfall, height_m)] ascending

    def height_at(self, hours_before_landfall: float) -> float:
        h = hours_before_landfall
        if self.points:
            pts = self.points
            if h <= pts[0][0]:
                return pts[0][1]
            if h >= pts[-1][0]:
                return pts[-1][1]
            for (h0, v0), (h1, v1) in zip(pts, pts[1:]):
                if h0 <= h <= h1 and h1 != h0:
                    return v0 + (v1 - v0) * (h - h0) / (h1 - h0)
            return pts[-1][1]
        # Default linear rising limb.
        if h <= _DEFAULT_RAMP_START_H:
            return 0.0
        if h >= _DEFAULT_RAMP_PEAK_H:
            return self.peak_m
        frac = (h - _DEFAULT_RAMP_START_H) / (_DEFAULT_RAMP_PEAK_H - _DEFAULT_RAMP_START_H)
        return self.peak_m * frac

    def crossing_hours_before(self, elevation_m: float, peak_m: Optional[float] = None) -> Optional[float]:
        """Hours before landfall when surge first reaches elevation_m.

        Returns None if the surge (at the given peak) never reaches it.
        """
        peak = self.peak_m if peak_m is None else peak_m
        if elevation_m > peak:
            return None
        if self.points:
            scaled = [(h, v * (peak / self.peak_m if self.peak_m > 0 else 1.0)) for h, v in self.points]
            for (h0, v0), (h1, v1) in zip(scaled, scaled[1:]):
                if v0 < elevation_m <= v1 and v1 != v0:
                    return h0 + (h1 - h0) * (elevation_m - v0) / (v1 - v0)
            if scaled and elevation_m <= scaled[0][1]:
                return scaled[0][0]
            return None
        frac = elevation_m / peak if peak > 0 else 0.0
        return _DEFAULT_RAMP_START_H + frac * (_DEFAULT_RAMP_PEAK_H - _DEFAULT_RAMP_START_H)


def _fmt_t(hours_before: Optional[float], landfall_dt: datetime) -> str:
    # hours_before is stored as hours-relative-to-landfall (negative == before).
    # e.g. -14.0 renders "T-14.0h" and resolves to landfall_dt + (-14h).
    if hours_before is None:
        return "NONE"
    abs_dt = landfall_dt + timedelta(hours=hours_before)
    return f"T{hours_before:.1f}h ({abs_dt.strftime('%Y-%m-%dT%H:%MZ')})"


def _pluvial_crossing_hours_before(rain_mm: Optional[float]) -> Optional[float]:
    """Hours before landfall when accumulating rainfall first ponds past the
    impassable threshold on flat surfaces (linear accumulation over 48h)."""
    if not rain_mm or rain_mm <= 0:
        return None
    total_depth_m = rain_mm * _RUNOFF_COEFF / 1000.0
    if total_depth_m <= _PLUVIAL_IMPASSABLE_M:
        return None
    return 48.0 * (1.0 - _PLUVIAL_IMPASSABLE_M / total_depth_m)


def build_assessment(
    layers: GeeLayers,
    met: MeteorologicalData,
    settings: Settings,
    now: Optional[datetime] = None,
) -> AssessmentOutput:
    now = now or datetime.now(timezone.utc)

    # --- Landfall anchor ------------------------------------------------- #
    eta_h = met.eta_landfall_hours
    eta_source = "feed"
    if eta_h is None:
        last = met.trajectory[-1]
        try:
            last_dt = datetime.strptime(last.timestamp_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            last_dt = now
        eta_h = max(0.0, (last_dt - now).total_seconds() / 3600.0)
        eta_source = "derived from last trajectory point"
    landfall_dt = now + timedelta(hours=eta_h)

    surge = met.storm_surge
    peak = surge.peak_height_m
    ci = surge.confidence_interval_m or None
    curve = SurgeCurve(peak if peak is not None else 0.0, surge.surge_curve_central)
    surge_basis = (
        f"surge curve from feed ({len(curve.points)} points)" if curve.points
        else "default rising limb T-18h→T-4h (no curve in feed)"
    )
    ci_txt = f", CI [{ci[0]:.1f}, {ci[1]:.1f}] m" if ci else ""
    log.info(
        "assessment: storm=%s eta=%.1fh (%s) peak=%s %s",
        met.storm_id, eta_h, eta_source, peak, ci_txt,
    )

    # --- Vulnerability assessment ---------------------------------------- #
    vulnerability: list[VulnerabilityItem] = []
    at_risk_assets: list[tuple[InfrastructureAsset, float]] = []  # (asset, crossing_h)
    safe_shelters: list[InfrastructureAsset] = []

    for asset in layers.infrastructure:
        elev = asset.elevation_m
        if elev is None:
            vulnerability.append(
                VulnerabilityItem(
                    asset_id=asset.id, asset_type=asset.type, asset_elevation_m=None,
                    expected_surge_m=peak if peak is not None else 0.0, inundation_margin_m=None,
                    failure_mode="Unknown — no DEM elevation sampled for this asset.",
                    anticipated_failure_window="UNKNOWN",
                    basis=f"No elevation in layer '{layers.dem.id}' and no geometry to sample.",
                )
            )
            continue
        if peak is None:
            vulnerability.append(
                VulnerabilityItem(
                    asset_id=asset.id, asset_type=asset.type, asset_elevation_m=elev,
                    expected_surge_m=0.0, inundation_margin_m=None,
                    failure_mode="Not evaluable — feed publishes no numeric surge height.",
                    anticipated_failure_window="UNKNOWN (no surge value)",
                    basis=f"Elevation {elev:.1f} m from '{layers.dem.id}'; surge unavailable ({surge.qualitative or 'no data'}).",
                )
            )
            continue

        margin = elev - peak
        cross = curve.crossing_hours_before(elev)
        if margin < 0 and cross is not None:
            # CI propagation: higher peak -> earlier crossing, lower -> later.
            early = late = None
            if ci:
                early = curve.crossing_hours_before(elev, peak_m=ci[1])
                late = curve.crossing_hours_before(elev, peak_m=ci[0])
            half = (late - early) / 2.0 if (early is not None and late is not None) else None
            window = _fmt_t(cross, landfall_dt) + (f" ±{half:.1f}h" if half is not None else "")
            serves = f" Serves: {', '.join(asset.serves)}." if asset.serves else ""
            beds = f" Capacity at risk: {asset.capacity_beds} beds." if asset.capacity_beds else ""
            vulnerability.append(
                VulnerabilityItem(
                    asset_id=asset.id, asset_type=asset.type, asset_elevation_m=elev,
                    expected_surge_m=peak, inundation_margin_m=margin,
                    failure_mode=(
                        f"Surge inundation ({peak:.1f} m > {elev:.1f} m): saltwater ingress, "
                        f"loss of function.{serves}{beds}"
                    ),
                    anticipated_failure_window=window,
                    basis=(
                        f"GEE '{layers.dem.id}' elevation {elev:.1f} m at asset {asset.id}; "
                        f"{surge_basis}; peak {peak:.1f} m{ci_txt} (storm {met.storm_id})."
                    ),
                )
            )
            at_risk_assets.append((asset, cross))
        else:
            note = "No surge failure pathway (asset above peak surge)."
            pluv = _pluvial_crossing_hours_before(met.predicted_rainfall_mm_48h)
            if pluv is not None:
                note += (
                    f" Pluvial monitoring: {met.predicted_rainfall_mm_48h:.0f} mm/48h "
                    f"(runoff proxy {_RUNOFF_COEFF:.0%}) exceeds ponding threshold at {_fmt_t(pluv, landfall_dt)}."
                )
            if asset.type == "medical_shelter":
                safe_shelters.append(asset)
            vulnerability.append(
                VulnerabilityItem(
                    asset_id=asset.id, asset_type=asset.type, asset_elevation_m=elev,
                    expected_surge_m=peak, inundation_margin_m=margin,
                    failure_mode=note,
                    anticipated_failure_window="NONE (surge)",
                    basis=(
                        f"GEE '{layers.dem.id}' elevation {elev:.1f} m; peak surge {peak:.1f} m; "
                        f"margin +{margin:.1f} m (storm {met.storm_id})."
                    ),
                )
            )

    # --- Evacuation pathways ---------------------------------------------- #
    pluv_cross = _pluvial_crossing_hours_before(met.predicted_rainfall_mm_48h)
    ci_upper = ci[1] if ci else peak
    pathways: list[EvacuationPathway] = []
    risky: list[tuple[str, Optional[float], float, str]] = []  # (road, cross_h, depth, cause)
    safe_routes: list[str] = []

    for road in layers.arterial_roads:
        elev = road.dem_elevation_m
        if elev is None:
            pathways.append(
                EvacuationPathway(
                    road_id=road.id, dem_elevation_m=None, projected_first_flood_time=None,
                    flood_depth_m=0.0, designation="SAFE_ROUTE", clearance_priority=None,
                    note="No DEM elevation available; route unverified — do not designate as safe.",
                )
            )
            continue
        if peak is not None and elev < peak:
            cross = curve.crossing_hours_before(elev)
            depth = peak - elev
            risky.append((road.id, cross, depth, f"surge {peak:.1f} m vs {elev:.1f} m DEM"))
        elif pluv_cross is not None and (ci_upper is None or elev < ci_upper + 1.0):
            risky.append((road.id, pluv_cross, _PLUVIAL_IMPASSABLE_M, "pluvial accumulation"))
        elif peak is not None and ci and elev >= ci_upper:
            safe_routes.append(road.id)
        elif peak is not None:
            safe_routes.append(road.id)

    risky.sort(key=lambda r: (r[1] is None, r[1] if r[1] is not None else 0.0))
    for rank, (road_id, cross, depth, cause) in enumerate(risky, start=1):
        pathways.append(
            EvacuationPathway(
                road_id=road_id,
                dem_elevation_m=next((r.dem_elevation_m for r in layers.arterial_roads if r.id == road_id), None),
                projected_first_flood_time=_fmt_t(cross, landfall_dt) if cross is not None else "UNKNOWN",
                flood_depth_m=round(depth, 2),
                designation="FLOOD_RISK",
                clearance_priority=rank,
                note=f"{cause} per '{layers.dem.id}' and storm {met.storm_id}.",
            )
        )
    for road_id in safe_routes:
        elev = next(r.dem_elevation_m for r in layers.arterial_roads if r.id == road_id)
        pathways.append(
            EvacuationPathway(
                road_id=road_id, dem_elevation_m=elev, projected_first_flood_time=None,
                flood_depth_m=0.0, designation="SAFE_ROUTE", clearance_priority=None,
                note=(
                    f"Above surge peak{f' and CI upper bound {ci_upper:.1f} m' if ci else ''} "
                    f"per '{layers.dem.id}'. Designated inland routing."
                ),
            )
        )

    # --- Parametric triggers ----------------------------------------------- #
    def _action_met(tranche: str) -> str:
        return f"Threshold met — execute {tranche} payout release to pre-approved accounts immediately."

    triggers: list[TriggerStatus] = [
        TriggerStatus(
            trigger_id="TRG-WIND-SUSTAINED",
            predefined_threshold=f"sustained winds >= {settings.trigger_wind_sustained_kmh:.0f} km/h",
            observed_value=met.sustained_wind_kmh,
            status="MET" if met.sustained_wind_kmh >= settings.trigger_wind_sustained_kmh else "NOT_MET",
            liquidity_action=(
                _action_met("tranche T1")
                if met.sustained_wind_kmh >= settings.trigger_wind_sustained_kmh
                else "No release; continue monitoring at next advisory cycle."
            ),
            verification_source=f"{met.source} (storm {met.storm_id})",
        ),
        TriggerStatus(
            trigger_id="TRG-WIND-GUST",
            predefined_threshold=f"wind gusts >= {settings.trigger_wind_gust_kmh:.0f} km/h",
            observed_value=met.wind_gust_kmh,
            status=(
                "NOT_EVALUABLE" if met.wind_gust_kmh is None
                else "MET" if met.wind_gust_kmh >= settings.trigger_wind_gust_kmh else "NOT_MET"
            ),
            liquidity_action=(
                "No gust value in feed; re-evaluate when available." if met.wind_gust_kmh is None
                else _action_met("gust-linked tranche") if met.wind_gust_kmh >= settings.trigger_wind_gust_kmh
                else "No release; continue monitoring."
            ),
            verification_source=f"{met.source} (storm {met.storm_id})",
        ),
        TriggerStatus(
            trigger_id="TRG-RAIN-48H",
            predefined_threshold=f"48h rainfall accumulation >= {settings.trigger_rainfall_mm_48h:.0f} mm",
            observed_value=met.predicted_rainfall_mm_48h,
            status=(
                "NOT_EVALUABLE" if met.predicted_rainfall_mm_48h is None
                else "MET" if met.predicted_rainfall_mm_48h >= settings.trigger_rainfall_mm_48h else "NOT_MET"
            ),
            liquidity_action=(
                "No rainfall value in feed; re-evaluate when available." if met.predicted_rainfall_mm_48h is None
                else _action_met("tranche T3") if met.predicted_rainfall_mm_48h >= settings.trigger_rainfall_mm_48h
                else "No release; continue monitoring."
            ),
            verification_source=f"{met.source} (storm {met.storm_id})",
        ),
    ]
    if peak is None:
        triggers.append(
            TriggerStatus(
                trigger_id="TRG-SURGE-PEAK",
                predefined_threshold=f"storm surge >= {settings.trigger_surge_m:.1f} m",
                observed_value=None,
                status="NOT_EVALUABLE",
                liquidity_action="Feed publishes no numeric surge; stage nothing on surge basis.",
                verification_source=surge.qualitative or f"{met.source} (no surge value)",
            )
        )
    elif ci and ci[0] < settings.trigger_surge_m <= peak:
        triggers.append(
            TriggerStatus(
                trigger_id="TRG-SURGE-PEAK",
                predefined_threshold=f"storm surge >= {settings.trigger_surge_m:.1f} m",
                observed_value=peak,
                status="CONDITIONAL_MET",
                liquidity_action=(
                    f"Stage tranche T2; release on observation (gauge) or p50 multi-model consensus "
                    f">= {settings.trigger_surge_m:.1f} m — CI lower bound {ci[0]:.1f} m precludes unconditional release."
                ),
                verification_source=f"{met.source} CI {ci} (storm {met.storm_id})",
            )
        )
    elif peak >= settings.trigger_surge_m:
        triggers.append(
            TriggerStatus(
                trigger_id="TRG-SURGE-PEAK",
                predefined_threshold=f"storm surge >= {settings.trigger_surge_m:.1f} m",
                observed_value=peak,
                status="MET",
                liquidity_action=_action_met("tranche T2"),
                verification_source=f"{met.source} (storm {met.storm_id})",
            )
        )
    else:
        triggers.append(
            TriggerStatus(
                trigger_id="TRG-SURGE-PEAK",
                predefined_threshold=f"storm surge >= {settings.trigger_surge_m:.1f} m",
                observed_value=peak,
                status="NOT_MET",
                liquidity_action="No release; continue monitoring.",
                verification_source=f"{met.source} (storm {met.storm_id})",
            )
        )

    # --- Automated advisories ---------------------------------------------- #
    def lead(cross_h: Optional[float]) -> float:
        # cross_h is negative (hours before landfall); crossing occurs at
        # eta_h + cross_h hours from now. Lead time is that horizon, floored at 0.
        if cross_h is None:
            return 0.0
        return max(0.0, round(eta_h + cross_h, 1))

    advisories: list[Advisory] = []
    rank = 1

    # 1. Shelters at risk first (life safety).
    at_risk_shelters = [(a, c) for a, c in at_risk_assets if a.type == "medical_shelter"]
    at_risk_shelters.sort(key=lambda t: t[1])
    for asset, cross in at_risk_shelters:
        dest = safe_shelters[0].id if safe_shelters else "nearest safe shelter (none identified — verify)"
        safe_roads = ", ".join(safe_routes) if safe_routes else "verified high-ground routes"
        advisories.append(
            Advisory(
                priority_rank=rank, recipient=f"Shelter operator / EOC ({asset.id})",
                lead_time_hours=lead(cross),
                directive=(
                    f"Relocate all occupants and medical assets from {asset.id} "
                    f"(elev {asset.elevation_m:.1f} m, surge {peak:.1f} m) to {dest} "
                    f"by {_fmt_t(cross, landfall_dt)}. Route via {safe_roads} only."
                ),
                linked_assessment_ref=asset.id,
            )
        )
        rank += 1

    # 2. Grid assets.
    at_risk_grid = [(a, c) for a, c in at_risk_assets if a.type in ("substation", "power_feeder")]
    at_risk_grid.sort(key=lambda t: t[1])
    for asset, cross in at_risk_grid:
        sectors = ", ".join(asset.serves) if asset.serves else "affected sectors"
        advisories.append(
            Advisory(
                priority_rank=rank, recipient=f"Grid operator ({asset.id})",
                lead_time_hours=lead(cross),
                directive=(
                    f"Pre-emptively de-energize {asset.id} before {_fmt_t(cross, landfall_dt)} "
                    f"to prevent saltwater energization faults; schedule restoration for {sectors}."
                ),
                linked_assessment_ref=asset.id,
            )
        )
        rank += 1

    # 3. Road closures.
    surge_roads = [r for r in risky if r[1] is not None]
    if surge_roads:
        first_id, first_cross, _, _ = surge_roads[0]
        closures = "; ".join(f"{rid} at {_fmt_t(c, landfall_dt)}" for rid, c, _, _ in surge_roads)
        advisories.append(
            Advisory(
                priority_rank=rank, recipient="Public works department",
                lead_time_hours=lead(first_cross),
                directive=(
                    f"Deploy barriers and close: {closures}. Erect reroute signage to "
                    f"{'/'.join(safe_routes) if safe_routes else 'verified inland routes'} at all coastal approaches."
                ),
                linked_assessment_ref=", ".join(r[0] for r in surge_roads),
            )
        )
        rank += 1

    # 4. Insurance desk.
    met_triggers = [t for t in triggers if t.status == "MET"]
    cond_triggers = [t for t in triggers if t.status == "CONDITIONAL_MET"]
    if met_triggers or cond_triggers:
        met_txt = ", ".join(t.trigger_id for t in met_triggers)
        cond_txt = ", ".join(t.trigger_id for t in cond_triggers)
        directive_parts = []
        if met_triggers:
            directive_parts.append(f"Execute releases now for MET triggers: {met_txt}.")
        if cond_triggers:
            directive_parts.append(f"Stage payouts pending observation confirmation for: {cond_txt}.")
        advisories.append(
            Advisory(
                priority_rank=rank, recipient="Parametric insurance desk", lead_time_hours=0.0,
                directive=" ".join(directive_parts),
                linked_assessment_ref=", ".join(t.trigger_id for t in met_triggers + cond_triggers),
            )
        )
        rank += 1

    # 5. Public evacuation comms — deadline = earliest at-risk asset/road crossing.
    all_cross = [c for _, c in at_risk_assets] + [r[1] for r in surge_roads]
    all_cross = [c for c in all_cross if c is not None]
    if all_cross and safe_routes:
        first = min(all_cross)
        advisories.append(
            Advisory(
                priority_rank=rank, recipient="Public communications", lead_time_hours=lead(first),
                directive=(
                    f"Broadcast immediately: all assets/zones below {peak:.1f} m elevation must evacuate by "
                    f"{_fmt_t(first, landfall_dt)}; inland routing via {' and '.join(safe_routes)}."
                ),
                linked_assessment_ref=f"{layers.dem.id} zoning",
            )
        )
        rank += 1

    return AssessmentOutput(
        vulnerability_assessment=vulnerability,
        parametric_triggers=triggers,
        evacuation_pathways=pathways,
        automated_advisories=advisories,
    )
