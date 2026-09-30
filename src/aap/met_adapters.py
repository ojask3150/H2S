"""Meteorological feed adapters.

Each adapter returns a validated :class:`MeteorologicalData`. Adapters are
tried in the order given by ``MET_SOURCES``; the first success wins.

Supported sources
-----------------
nhc    : NOAA National Hurricane Center text products (TCM forecast/advisory
         + public advisory for rainfall). No API key. Storm resolved via
         https://www.nhc.noaa.gov/CurrentStorms.json (activeStorms[].binNumber
         maps to the text-product slot, e.g. AT3 -> MIATCPAT3.shtml).
jtwc   : JTWC ATCF a/b-deck plain-text files. No API key. URL pattern is
         configurable (MET_JTWC_URL) because JTWC hosting paths change.
custom : Any HTTP endpoint returning the meteorological_json payload
         (the authoritative internal aggregator that fuses model surge/rain).

Note: NHC/JTWC public products do not publish numeric storm-surge heights.
Those adapters therefore leave ``storm_surge.peak_height_m`` unset and the
analysis pipeline marks surge-dependent triggers NOT_EVALUABLE. Supply the
custom feed (or set surge manually) for full trigger evaluation.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx

from .config import Settings
from .models import MeteorologicalData, StormSurge, TrackPoint

log = logging.getLogger(__name__)

_MET_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "met_static.json"

_UA = {"User-Agent": "anticipatory-action-platform/0.1 (ingest)"}

_NHC_BASIN_PRODUCT = {"al": "AT", "ep": "EP", "cp": "CP"}


class FeedError(RuntimeError):
    """Raised when a feed cannot be fetched or parsed."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _kn_to_kmh(kn: float) -> float:
    return kn * 1.852


def _in_to_mm(inches: float) -> float:
    return inches * 25.4


async def _fetch_text(url: str, client: httpx.AsyncClient, timeout: float = 20.0) -> str:
    resp = await client.get(url, timeout=timeout, headers=_UA, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def _parse_coord(token: str, pos: str, neg: str) -> float:
    """Parse an ATCF coordinate token.

    Handles both real ATCF integer-tenths with a hemisphere suffix
    ('234N' -> 23.4) and decimal-degree tokens with an optional hemisphere or
    sign ('17.8N', '-23.4', '88.1E'). A hemisphere of ``neg`` (S / W) negates.
    """
    m = re.fullmatch(rf"(-?\d+(?:\.\d+)?)\s*([{pos}{neg}{pos.lower()}{neg.lower()}]?)", token.strip())
    if not m:
        raise FeedError(f"bad coordinate token: {token!r}")
    val = float(m.group(1))
    hemi = m.group(2).upper()
    # Integer + hemisphere is the real ATCF tenths-of-degrees convention.
    if hemi and "." not in token:
        val /= 10.0
    return -val if hemi == neg else val


def _parse_lat(token: str) -> float:
    return _parse_coord(token, "N", "S")


def _parse_lon(token: str) -> float:
    return _parse_coord(token, "E", "W")


# --------------------------------------------------------------------------- #
# NHC adapter
# --------------------------------------------------------------------------- #
_RE_ISSUE_TIME = re.compile(
    r"(\d{4})\s+UTC\s+(?:MON|TUE|WED|THU|FRI|SAT|SUN)\s+([A-Z]{3})\s+(\d{1,2})\s+(\d{4})", re.I
)
_RE_LOCATION = re.compile(r"LOCATION\.\.\.\s*([\d.]+)\s*([NS])\s+([\d.]+)\s*([WE])", re.I)
_RE_SUSTAINED = re.compile(r"MAXIMUM SUSTAINED WINDS\.\.\.\s*(\d+)\s*KT", re.I)
_RE_FCST_BLOCK = re.compile(
    r"FORECAST VALID\s+(\d{1,2}/\d{4}Z)\s+([\d.]+)\s*([NS])\s+([\d.]+)\s*([WE])", re.I
)
_RE_BLOCK_WIND = re.compile(r"MAX WIND\s+(\d+)\s+KT\.\.\.GUSTS\s+(\d+)\s+KT", re.I)
_RE_RAIN_INCHES = re.compile(r"(\d+)\s+TO\s+(\d+)\s+INCH(?:ES)?", re.I)
_RE_DT_TOKEN = re.compile(r"(\d{1,2})/(\d{2})(\d{2})Z")

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


def _issue_datetime(text: str) -> datetime:
    m = _RE_ISSUE_TIME.search(text)
    if not m:
        raise FeedError("NHC: cannot locate advisory issue time (expected 'HHMM UTC DDD MMM DD YYYY')")
    hhmm, mon, day, year = m.groups()
    month = _MONTHS.get(mon.upper())
    if month is None:
        raise FeedError(f"NHC: unknown month token {mon!r}")
    return datetime(int(year), month, int(day), int(hhmm[:2]), int(hhmm[2:]), tzinfo=timezone.utc)


def _signed_lat(val: str, hemi: str) -> float:
    v = float(val)
    return -v if hemi.upper() == "S" else v


def _signed_lon(val: str, hemi: str) -> float:
    v = float(val)
    return -v if hemi.upper() == "W" else v


def _fcst_datetime(issue: datetime, token: str) -> datetime:
    """Convert a DATE/TIME row token ('18/0600Z') to a UTC datetime.

    Handles month rollover: if the resulting date precedes the issue date,
    advance one calendar month (forecast tables span at most ~5 days).
    """
    m = _RE_DT_TOKEN.fullmatch(token.strip())
    if not m:
        raise FeedError(f"NHC: unexpected DATE/TIME token {token!r}")
    day, hh, mm = int(m.group(1)), int(m.group(2)), int(m.group(3))
    dt = issue.replace(day=1, hour=hh, minute=mm, second=0, microsecond=0) + timedelta(days=day - 1)
    if dt.date() < issue.date():
        yy, mo = (issue.year + 1, 1) if issue.month == 12 else (issue.year, issue.month + 1)
        dt = dt.replace(year=yy, month=mo)
    return dt


def parse_nhc_advisory(
    advisory_text: str,
    *,
    storm_id: str,
    public_text: Optional[str] = None,
) -> MeteorologicalData:
    """Parse an NHC forecast/advisory (TCM) text product into MeteorologicalData."""
    issue = _issue_datetime(advisory_text)

    m = _RE_LOCATION.search(advisory_text)
    if not m:
        raise FeedError("NHC: cannot locate current position (LOCATION... line)")
    lat = float(m.group(1)) * (1 if m.group(2).upper() == "N" else -1)
    lon = float(m.group(3)) * (1 if m.group(4).upper() == "E" else -1)
    trajectory: list[TrackPoint] = [
        TrackPoint(lat=lat, lon=lon, timestamp_utc=issue.strftime("%Y-%m-%dT%H:%M:%SZ"), status="current")
    ]

    ms = _RE_SUSTAINED.search(advisory_text)
    if not ms:
        raise FeedError("NHC: cannot locate MAXIMUM SUSTAINED WINDS")
    wind_kmh = _kn_to_kmh(float(ms.group(1)))
    gust_kmh = wind_kmh  # fallback; refined from forecast blocks below

    # Forecast blocks: "FORECAST VALID 18/0600Z <lat> N <lon> W" followed by
    # a "MAX WIND ... KT...GUSTS ... KT" line within the same block.
    blocks = _RE_FCST_BLOCK.finditer(advisory_text)
    found_forecast = False
    for blk in blocks:
        token = blk.group(1)
        block_text = advisory_text[blk.start(): blk.start() + 400]
        bmw = _RE_BLOCK_WIND.search(block_text)
        pt_wind = _kn_to_kmh(float(bmw.group(1))) if bmw else wind_kmh
        pt_gust = _kn_to_kmh(float(bmw.group(2))) if bmw else gust_kmh
        trajectory.append(
            TrackPoint(
                lat=_signed_lat(blk.group(2), blk.group(3)),
                lon=_signed_lon(blk.group(4), blk.group(5)),
                timestamp_utc=_fcst_datetime(issue, token).strftime("%Y-%m-%dT%H:%M:%SZ"),
                status="forecast",
            )
        )
        found_forecast = True
        # Carry the strongest forecast wind forward as the advisory-level value.
        wind_kmh = max(wind_kmh, pt_wind)
        gust_kmh = max(gust_kmh, pt_gust)
    if not found_forecast:
        log.warning("NHC: no FORECAST VALID blocks found; trajectory is current position only")

    # Rainfall: best-effort from the public advisory (reported in inches).
    rain_mm: Optional[float] = None
    if public_text:
        rain = _RE_RAIN_INCHES.search(public_text)
        if rain:
            rain_mm = _in_to_mm(float(rain.group(2)))  # conservative: upper bound

    return MeteorologicalData(
        storm_id=storm_id,
        source="NHC (forecast/advisory TCM)",
        trajectory=trajectory,
        eta_landfall_hours=None,  # NHC TCM does not state a landfall ETA
        sustained_wind_kmh=wind_kmh,
        wind_gust_kmh=gust_kmh,
        predicted_rainfall_mm_48h=rain_mm,
        storm_surge=StormSurge(peak_height_m=None, qualitative="Not published in NHC text products"),
    )


async def fetch_nhc(settings: Settings, client: httpx.AsyncClient) -> MeteorologicalData:
    """Fetch the current NHC storm in the configured basin and parse it."""
    basin = settings.met_nhc_basin.lower()
    product = _NHC_BASIN_PRODUCT.get(basin)
    if product is None:
        raise FeedError(f"NHC: unsupported basin {basin!r} (use al|ep|cp)")

    # Resolve the active storm -> text-product slot (binNumber, e.g. 'AT3').
    index_txt = await _fetch_text("https://www.nhc.noaa.gov/CurrentStorms.json", client)
    storms = json.loads(index_txt).get("activeStorms") or []
    if not storms:
        raise FeedError(f"NHC: no active storms (basin filter: {basin!r})")
    wanted = settings.met_nhc_storm_id.strip().lower()
    entry = next((s for s in storms if str(s.get("id", "")).lower() == wanted), None)
    if entry is None and wanted:
        raise FeedError(f"NHC: configured storm id {wanted!r} is not in activeStorms")
    if entry is None:
        entry = next((s for s in storms if str(s.get("id", "")).lower().startswith(basin)), storms[0])
    bin_number = str(entry.get("binNumber", "")).upper()
    if not re.fullmatch(rf"{product}\d", bin_number):
        raise FeedError(f"NHC: storm {entry.get('id')!r} binNumber {bin_number!r} not in basin {basin!r}")
    slot = bin_number[-1]
    storm_id = str(entry.get("id", "unknown"))

    advisory_text = await _fetch_text(f"https://www.nhc.noaa.gov/text/MIATCP{product}{slot}.shtml", client)
    try:
        public_text = await _fetch_text(f"https://www.nhc.noaa.gov/text/MIAPDF{product}{slot}.shtml", client)
    except httpx.HTTPError:
        public_text = None
    return parse_nhc_advisory(advisory_text, storm_id=storm_id, public_text=public_text)


# --------------------------------------------------------------------------- #
# JTWC ATCF adapter
# --------------------------------------------------------------------------- #
# ATCF deck columns (0-based) we consume.
_ATCF_DT, _ATCF_TAU, _ATCF_LAT, _ATCF_LON, _ATCF_VMAX, _ATCF_GUSTS = (2, 5, 6, 7, 8, 20)


def parse_atcf_deck(text: str, *, storm_id: str, source: str) -> MeteorologicalData:
    """Parse an ATCF a/b-deck file (CSV, lat/lon in tenths of degrees)."""
    trajectory: list[TrackPoint] = []
    wind_max = 0.0
    gust_max: Optional[float] = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(","):
            continue
        cols = [c.strip() for c in line.split(",")]
        if len(cols) <= max(_ATCF_LAT, _ATCF_LON, _ATCF_VMAX):
            continue
        try:
            tau = int(cols[_ATCF_TAU] or 0)
            ts = datetime.strptime(cols[_ATCF_DT], "%Y%m%d%H").replace(tzinfo=timezone.utc)
            lat = _parse_lat(cols[_ATCF_LAT])
            lon = _parse_lon(cols[_ATCF_LON])
            vmax = float(cols[_ATCF_VMAX])
        except (ValueError, FeedError, IndexError):
            continue  # skip header/garbage rows silently
        gust = None
        if _ATCF_GUSTS < len(cols) and cols[_ATCF_GUSTS].isdigit() and int(cols[_ATCF_GUSTS]) > 0:
            gust = _kn_to_kmh(float(cols[_ATCF_GUSTS]))
        trajectory.append(
            TrackPoint(
                lat=lat,
                lon=lon,
                timestamp_utc=ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                status="current" if tau == 0 else "forecast",
            )
        )
        wind_max = max(wind_max, _kn_to_kmh(vmax))
        gust_max = max(gust_max or 0.0, gust or 0.0) if (gust or gust_max) else gust_max

    if not trajectory:
        raise FeedError(f"JTWC: no parseable rows in deck for {storm_id!r}")
    trajectory.sort(key=lambda p: p.timestamp_utc)

    return MeteorologicalData(
        storm_id=storm_id,
        source=source,
        trajectory=trajectory,
        eta_landfall_hours=None,
        sustained_wind_kmh=wind_max,
        wind_gust_kmh=gust_max,
        predicted_rainfall_mm_48h=None,
        storm_surge=StormSurge(peak_height_m=None, qualitative="Not published in ATCF decks"),
    )


async def fetch_jtwc(settings: Settings, client: httpx.AsyncClient) -> MeteorologicalData:
    """Fetch a JTWC ATCF deck. URL pattern configurable via MET_JTWC_URL."""
    url = settings.met_jtwc_url.strip()
    if not url:
        year = datetime.now(timezone.utc).year
        storm = settings.met_jtwc_storm_id.strip()
        if not storm:
            raise FeedError("JTWC: MET_JTWC_STORM_ID or MET_JTWC_URL must be set")
        storm = storm if year in storm else f"{storm}{year}"
        url = f"https://www.nrlmry.navy.mil/atcf_web/docs/current_trak/b{settings.met_jtwc_basin}{storm}.dat"
    text = await _fetch_text(url, client)
    return parse_atcf_deck(
        text,
        storm_id=settings.met_jtwc_storm_id or "jtwc",
        source="JTWC (ATCF deck)",
    )


# --------------------------------------------------------------------------- #
# Custom JSON adapter + orchestrator
# --------------------------------------------------------------------------- #
async def fetch_custom(settings: Settings, client: httpx.AsyncClient) -> MeteorologicalData:
    """Fetch the meteorological_json payload from the configured endpoint."""
    if not settings.met_custom_url:
        raise FeedError("custom: MET_CUSTOM_URL is not set")
    resp = await client.get(settings.met_custom_url, timeout=20.0, headers=_UA)
    resp.raise_for_status()
    try:
        return MeteorologicalData.model_validate(resp.json())
    except ValueError as exc:
        raise FeedError(f"custom: payload failed schema validation: {exc}") from exc


async def fetch_static(settings: Settings, client: httpx.AsyncClient) -> MeteorologicalData:
    """Load the bundled offline met fixture (Bay of Bengal demo storm).

    Requires no network or credentials; pairs with GEE_MODE=static so the whole
    platform (and the frontend) runs fully offline.
    """
    try:
        return MeteorologicalData.model_validate(json.loads(_MET_FIXTURE.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise FeedError(f"static: cannot load met fixture: {exc}") from exc


async def fetch_met(settings: Settings, client: httpx.AsyncClient | None = None) -> MeteorologicalData:
    """Try each configured source in order; first success wins."""
    owned = client is None
    if owned:
        client = httpx.AsyncClient()
    try:
        errors: list[str] = []
        for source in settings.met_source_list:
            adapter = {
                "nhc": fetch_nhc,
                "jtwc": fetch_jtwc,
                "custom": fetch_custom,
                "static": fetch_static,
            }.get(source)
            if adapter is None:
                errors.append(f"{source}: unknown source")
                continue
            try:
                data = await adapter(settings, client)
                log.info("met feed acquired from %s (storm %s)", source, data.storm_id)
                return data
            except (FeedError, httpx.HTTPError, ValueError) as exc:
                log.warning("met source %s failed: %s", source, exc)
                errors.append(f"{source}: {exc}")
        raise FeedError("all configured met sources failed: " + "; ".join(errors))
    finally:
        if owned:
            await client.aclose()
