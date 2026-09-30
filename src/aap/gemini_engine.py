"""Gemini 3.7 Flash multimodal reasoning engine.

Accepts the GEE map exports (as images) plus the current meteorological data
(as JSON) and asks ``gemini-3.7-flash`` to produce the four mandated output
keys via structured JSON.

Design notes
------------
* The Google GenAI SDK import is lazy so the service (and the test suite) runs
  without the package installed. Install it with ``pip install google-genai``.
* Per the platform contract we send ``thinking_level="high"`` and deliberately
  DO NOT pass the legacy ``temperature`` / ``top_p`` sampling parameters — a
  disaster-reasoning engine must be deterministic, not sampled.
* ``response_mime_type="application/json"`` + a ``response_schema`` force the
  model to return exactly the :class:`AssessmentOutput` shape.
* When no API key is configured (or ``REASONING_ENGINE=deterministic``) we fall
  back to the local :func:`aap.analysis.build_assessment` pipeline so the
  endpoint always returns a valid, auditable assessment.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .analysis import build_assessment
from .config import Settings
from .models import AssessmentOutput, GeeLayers, MeteorologicalData

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# System prompt — the reasoning engine's operating instructions.
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """\
ROLE: You are the core analytical reasoning engine for a coastal disaster \
anticipatory action platform.

INPUT CAPABILITY: You receive a multimodal payload:
1. Google Earth Engine (GEE) satellite imagery highlighting critical \
infrastructure (power grids, arterial roads, medical shelters) and topological \
elevation.
2. Real-time JSON meteorological data (cyclone trajectory, sustained wind \
speeds, predicted rainfall accumulation, storm surge height) plus a structured \
GEE layer inventory (DEM-referenced elevations for each asset and road).

TASK: Analyze the intersection of the storm trajectory, the elevation data, \
and the critical infrastructure map. Shift the focus entirely to PRE-LANDFALL \
anticipatory action.

OUTPUT REQUIREMENTS: Output a strictly formatted JSON object with these keys:
- "vulnerability_assessment": specific infrastructure failure pathways (e.g. \
"Substation A is at 1.5m elevation; expected surge is 2.2m. Anticipated \
failure window: T-minus 14 hours").
- "parametric_triggers": whether current meteorology meets pre-defined \
thresholds for instant parametric insurance liquidity (e.g. sustained winds \
> 150km/h).
- "evacuation_pathways": which arterial roads flood first based on the DEM, \
and safe inland routing.
- "automated_advisories": concise, prioritized warning text for municipal \
authorities, explicitly stating actions (grid shutdown, shelter activation) \
grounded in the vulnerability assessment.

CONSTRAINTS: Be analytical, precise, and objective. No conversational filler. \
Base every vulnerability claim directly on the visual intersections in the \
provided GEE maps and the provided meteorological JSON. Every T-minus window, \
elevation, and surge value must trace to a provided input — never invent one.\
"""


@dataclass
class MapExport:
    """A GEE map export image to include in the multimodal payload."""

    data: bytes
    mime_type: str = "image/png"
    caption: str = ""

    @classmethod
    def from_base64(cls, b64: str, mime_type: str = "image/png", caption: str = "") -> "MapExport":
        return cls(data=base64.b64decode(b64), mime_type=mime_type, caption=caption)


@dataclass
class ReasoningResult:
    output: AssessmentOutput
    engine: str  # "gemini" | "deterministic"
    model: Optional[str] = None
    thinking_level: Optional[str] = None
    notes: list[str] = field(default_factory=list)


class GeminiError(RuntimeError):
    """Raised when the Gemini engine is required but cannot be used."""


def _build_user_prompt(met: MeteorologicalData, layers: GeeLayers) -> str:
    now = datetime.now(timezone.utc).isoformat()
    return (
        f"CURRENT TIME (UTC): {now}\n\n"
        "METEOROLOGICAL JSON:\n"
        f"{met.model_dump_json(indent=2)}\n\n"
        "GEE LAYER INVENTORY (DEM-referenced):\n"
        f"{layers.model_dump_json(indent=2)}\n\n"
        "The attached images are the GEE map exports for this scene. "
        "Produce the four-key JSON assessment now."
    )


def _reason_with_gemini(
    settings: Settings,
    met: MeteorologicalData,
    layers: GeeLayers,
    map_exports: list[MapExport],
) -> ReasoningResult:
    from google import genai  # lazy: absent in deterministic mode
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key)

    parts: list[object] = [types.Part.from_text(text=_build_user_prompt(met, layers))]
    for export in map_exports:
        if export.caption:
            parts.append(types.Part.from_text(text=f"[MAP] {export.caption}"))
        parts.append(types.Part.from_bytes(data=export.data, mime_type=export.mime_type))

    # thinking_level="high"; legacy temperature/top_p intentionally omitted.
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        thinking_config=types.ThinkingConfig(thinking_level=settings.gemini_thinking_level),
        response_mime_type="application/json",
        response_schema=AssessmentOutput,
    )

    resp = client.models.generate_content(
        model=settings.gemini_model,
        contents=[types.Content(role="user", parts=parts)],
        config=config,
    )

    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, AssessmentOutput):
        output = parsed
    else:
        raw = resp.text
        output = AssessmentOutput.model_validate(json.loads(raw))

    log.info(
        "gemini assessment: model=%s thinking=%s images=%d",
        settings.gemini_model, settings.gemini_thinking_level, len(map_exports),
    )
    return ReasoningResult(
        output=output,
        engine="gemini",
        model=settings.gemini_model,
        thinking_level=settings.gemini_thinking_level,
        notes=[f"{len(map_exports)} GEE map export(s) analyzed"],
    )


def reason(
    settings: Settings,
    met: MeteorologicalData,
    layers: GeeLayers,
    map_exports: Optional[list[MapExport]] = None,
) -> ReasoningResult:
    """Produce an assessment, preferring Gemini and falling back to the local pipeline.

    Selection follows ``settings.reasoning_engine``:
      * ``deterministic`` -> always the local analysis pipeline.
      * ``gemini``        -> require Gemini; raise :class:`GeminiError` otherwise.
      * ``auto`` (default)-> Gemini when an API key is set, else deterministic.
    """
    map_exports = map_exports or []
    engine = settings.reasoning_engine.lower()
    has_key = bool(settings.gemini_api_key)

    if engine == "deterministic" or (engine == "auto" and not has_key):
        output = build_assessment(layers, met, settings)
        reason_note = "no GEMINI_API_KEY" if engine == "auto" else "REASONING_ENGINE=deterministic"
        return ReasoningResult(
            output=output,
            engine="deterministic",
            notes=[f"local analysis pipeline ({reason_note})"],
        )

    if engine == "gemini" and not has_key:
        raise GeminiError("REASONING_ENGINE=gemini requires GEMINI_API_KEY")

    try:
        return _reason_with_gemini(settings, met, layers, map_exports)
    except ModuleNotFoundError as exc:
        if engine == "gemini":
            raise GeminiError("google-genai is not installed (pip install google-genai)") from exc
        log.warning("google-genai unavailable; using deterministic pipeline: %s", exc)
        return ReasoningResult(
            output=build_assessment(layers, met, settings),
            engine="deterministic",
            notes=["google-genai not installed; deterministic fallback"],
        )
    except Exception as exc:  # network / API / parse failure
        if engine == "gemini":
            raise GeminiError(f"Gemini reasoning failed: {exc}") from exc
        log.warning("Gemini reasoning failed; using deterministic pipeline: %s", exc)
        return ReasoningResult(
            output=build_assessment(layers, met, settings),
            engine="deterministic",
            notes=[f"Gemini failed ({exc}); deterministic fallback"],
        )
