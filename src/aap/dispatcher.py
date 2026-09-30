"""Advisory dispatcher.

Turns an :class:`AssessmentOutput` into concrete outbound artifacts:

* localized SMS templates (<=320 chars, action-first) per advisory recipient;
* email templates (subject + body) carrying the linked assessment detail;
* parametric-insurance smart-contract trigger payloads for every MET or
  CONDITIONAL_MET trigger.

Nothing is actually transmitted or broadcast here — that is an outward-facing,
credential-gated action. This module STAGES the payloads (``status="STAGED"``)
so an operator or a downstream gateway with the right secrets can review and
release them. A MET trigger produces a ``READY_TO_EXECUTE`` contract call;
CONDITIONAL_MET produces a ``STAGED_PENDING_CONFIRMATION`` one.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from .models import Advisory, AssessmentOutput, TriggerStatus


# --------------------------------------------------------------------------- #
# Dispatch artifacts
# --------------------------------------------------------------------------- #
class SmsMessage(BaseModel):
    recipient: str
    priority_rank: int
    body: str  # <=320 chars, GSM-friendly
    linked_ref: str


class EmailMessage(BaseModel):
    recipient: str
    priority_rank: int
    subject: str
    body: str
    linked_ref: str


class ContractTrigger(BaseModel):
    trigger_id: str
    status: Literal["READY_TO_EXECUTE", "STAGED_PENDING_CONFIRMATION"]
    method: str
    threshold: str
    observed_value: Optional[float]
    tranche: str
    verification_source: str
    payload: dict  # the smart-contract call arguments (chain-agnostic)


class DispatchBundle(BaseModel):
    storm_ref: str
    sms: list[SmsMessage]
    email: list[EmailMessage]
    contract_triggers: list[ContractTrigger]
    summary: dict


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
_SMS_MAX = 320
_TRANCHE_FOR = {
    "TRG-WIND-SUSTAINED": "T1",
    "TRG-WIND-GUST": "T1-GUST",
    "TRG-RAIN-48H": "T3",
    "TRG-SURGE-PEAK": "T2",
}


def _sms_body(adv: Advisory, storm_ref: str) -> str:
    lead = f"T-{adv.lead_time_hours:.0f}h" if adv.lead_time_hours else "IMMEDIATE"
    body = f"[AAP/{storm_ref} P{adv.priority_rank} {lead}] {adv.directive}"
    if len(body) > _SMS_MAX:
        body = body[: _SMS_MAX - 1].rstrip() + "…"
    return body


def _email_body(adv: Advisory, storm_ref: str) -> str:
    return (
        f"Anticipatory Action Advisory — storm {storm_ref}\n"
        f"Priority: P{adv.priority_rank}\n"
        f"Recipient: {adv.recipient}\n"
        f"Lead time: {adv.lead_time_hours:.1f} h\n"
        f"Linked assessment: {adv.linked_assessment_ref}\n\n"
        f"DIRECTIVE:\n{adv.directive}\n\n"
        "This is an automated pre-landfall advisory. Confirm receipt and log the "
        "action taken in the EOC incident record."
    )


def _contract_payload(trg: TriggerStatus, storm_ref: str) -> dict:
    return {
        "storm_id": storm_ref,
        "trigger_id": trg.trigger_id,
        "tranche": _TRANCHE_FOR.get(trg.trigger_id, "UNSPECIFIED"),
        "threshold": trg.predefined_threshold,
        "observed_value": trg.observed_value,
        "verification_source": trg.verification_source,
        "requires_oracle_confirmation": trg.status == "CONDITIONAL_MET",
    }


def build_dispatch(output: AssessmentOutput, storm_ref: str = "UNKNOWN") -> DispatchBundle:
    """Render SMS/email templates and contract triggers from an assessment."""
    sms: list[SmsMessage] = []
    email: list[EmailMessage] = []
    for adv in output.automated_advisories:
        sms.append(
            SmsMessage(
                recipient=adv.recipient,
                priority_rank=adv.priority_rank,
                body=_sms_body(adv, storm_ref),
                linked_ref=adv.linked_assessment_ref,
            )
        )
        email.append(
            EmailMessage(
                recipient=adv.recipient,
                priority_rank=adv.priority_rank,
                subject=f"[P{adv.priority_rank}] AAP advisory — {storm_ref}",
                body=_email_body(adv, storm_ref),
                linked_ref=adv.linked_assessment_ref,
            )
        )

    contracts: list[ContractTrigger] = []
    for trg in output.parametric_triggers:
        if trg.status not in ("MET", "CONDITIONAL_MET"):
            continue
        contracts.append(
            ContractTrigger(
                trigger_id=trg.trigger_id,
                status="READY_TO_EXECUTE" if trg.status == "MET" else "STAGED_PENDING_CONFIRMATION",
                method="releaseParametricPayout",
                threshold=trg.predefined_threshold,
                observed_value=trg.observed_value,
                tranche=_TRANCHE_FOR.get(trg.trigger_id, "UNSPECIFIED"),
                verification_source=trg.verification_source,
                payload=_contract_payload(trg, storm_ref),
            )
        )

    return DispatchBundle(
        storm_ref=storm_ref,
        sms=sms,
        email=email,
        contract_triggers=contracts,
        summary={
            "advisories": len(output.automated_advisories),
            "sms": len(sms),
            "email": len(email),
            "contract_triggers_ready": sum(1 for c in contracts if c.status == "READY_TO_EXECUTE"),
            "contract_triggers_staged": sum(
                1 for c in contracts if c.status == "STAGED_PENDING_CONFIRMATION"
            ),
        },
    )
