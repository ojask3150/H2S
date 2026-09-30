"""Tests for the Gemini engine selection and the advisory dispatcher."""
from __future__ import annotations

from aap.config import Settings
from aap.dispatcher import build_dispatch
from aap.gemini_engine import reason
from aap.models import AssessmentOutput, GeeLayers, MeteorologicalData

from fixtures_payloads import LAYERS_PAYLOAD, MET_PAYLOAD


def _settings(**kw) -> Settings:
    base = dict(
        trigger_wind_sustained_kmh=150.0,
        trigger_wind_gust_kmh=200.0,
        trigger_rainfall_mm_48h=350.0,
        trigger_surge_m=2.0,
    )
    base.update(kw)
    return Settings(**base)


def _layers() -> GeeLayers:
    return GeeLayers.model_validate(LAYERS_PAYLOAD)


def _met() -> MeteorologicalData:
    return MeteorologicalData.model_validate(MET_PAYLOAD)


class TestReasoningEngineSelection:
    def test_deterministic_when_forced(self):
        res = reason(_settings(reasoning_engine="deterministic"), _met(), _layers())
        assert res.engine == "deterministic"
        assert isinstance(res.output, AssessmentOutput)
        assert res.output.vulnerability_assessment

    def test_auto_without_key_is_deterministic(self):
        res = reason(_settings(reasoning_engine="auto", gemini_api_key=""), _met(), _layers())
        assert res.engine == "deterministic"

    def test_gemini_without_key_raises(self):
        import pytest

        from aap.gemini_engine import GeminiError

        with pytest.raises(GeminiError):
            reason(_settings(reasoning_engine="gemini", gemini_api_key=""), _met(), _layers())


class TestDispatcher:
    def _bundle(self):
        settings = _settings()
        from aap.analysis import build_assessment

        out = build_assessment(_layers(), _met(), settings)
        return build_dispatch(out, storm_ref="SYNTH-DEMO-01")

    def test_sms_and_email_per_advisory(self):
        out = self._bundle()
        assert len(out.sms) == len(out.email)
        assert out.sms  # demo scenario produces advisories
        assert all(len(m.body) <= 320 for m in out.sms)
        assert all(m.subject and m.body for m in out.email)

    def test_contract_triggers_for_met_and_conditional(self):
        out = self._bundle()
        ids = {c.trigger_id for c in out.contract_triggers}
        # Wind-sustained (MET) and surge (CONDITIONAL_MET) both fire in the demo.
        assert "TRG-WIND-SUSTAINED" in ids
        statuses = {c.trigger_id: c.status for c in out.contract_triggers}
        assert statuses["TRG-WIND-SUSTAINED"] == "READY_TO_EXECUTE"
        assert statuses.get("TRG-SURGE-PEAK") == "STAGED_PENDING_CONFIRMATION"

    def test_not_met_triggers_excluded(self):
        out = self._bundle()
        ids = {c.trigger_id for c in out.contract_triggers}
        assert "TRG-WIND-GUST" not in ids  # NOT_MET in the demo (185 < 200)
