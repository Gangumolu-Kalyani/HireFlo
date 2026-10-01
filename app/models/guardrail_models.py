"""Pydantic models for guardrail results and human review.

These models are used throughout Phase 5 to carry structured guardrail
evaluation results and to construct the human-review summary object.

GuardrailResult
    Returned by every guardrail function.  Carries a pass/fail decision,
    severity classification, flag identifiers, and human-readable reasons.

HumanReview
    Aggregated summary presented to the human reviewer before they approve or
    reject an interview proposal.  This object will be displayed in the
    Streamlit UI in Phase 6.  Do NOT build the UI yet.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ── Severity ──────────────────────────────────────────────────────────────────

Severity = Literal["low", "medium", "high"]


# ── GuardrailResult ───────────────────────────────────────────────────────────


class GuardrailResult(BaseModel):
    """The structured result of a single guardrail evaluation.

    Fields
    ------
    passed
        True if the input/output passed the check.
    severity
        The highest severity found, or None if ``passed`` is True.
        Policy:
            low    → continue, record flag
            medium → continue with human review required
            high   → stop; set final_status = "GUARDRAIL_BLOCKED"
    flags
        Short, ASCII-safe flag identifiers.  One per distinct issue found.
        Example: ``["empty_evidence", "score_out_of_range"]``
    reasons
        Human-readable explanations corresponding to each flag.
    """

    passed: bool = Field(description="True if the check passed (no issues found).")
    severity: Severity | None = Field(
        default=None,
        description="Highest severity found, or None if the check passed.",
    )
    flags: list[str] = Field(
        default_factory=list,
        description="Short flag identifiers, one per distinct issue.",
    )
    reasons: list[str] = Field(
        default_factory=list,
        description="Human-readable descriptions corresponding to each flag.",
    )


# ── HumanReview ───────────────────────────────────────────────────────────────


class HumanReview(BaseModel):
    """Aggregated recruitment summary for the human reviewer.

    This is the object a human sees before deciding to approve or reject an
    interview proposal.  It consolidates candidate information, scores,
    guardrail findings, and the proposal into one place.

    Phase 6 (Streamlit UI) will render this object.
    Do NOT build the UI in Phase 5.

    Fields
    ------
    candidate
        Candidate name from the parsed profile.
    score
        Python-computed weighted score (0–5).  Never set by the LLM.
    recommendation
        Recommendation tier: ``strong_yes`` / ``yes`` / ``maybe`` / ``no``.
    evidence
        List of (criterion, score, evidence) triples for transparency.
    availability
        Available interview slots as a list of strings.
    proposed_slot
        The slot selected by the propose_interview node.
    guardrail_flags
        List of flag strings from all guardrail checks.
    fairness_flags
        List of flag strings from the fairness check specifically.
    approval_required
        Always True for interview proposals.
    status
        Mirrors ``final_status`` in the graph state.
    """

    candidate: str = Field(min_length=1, description="Candidate name.")
    score: float = Field(ge=0, le=5, description="Python-computed weighted score (0–5).")
    recommendation: str = Field(description="Recommendation tier.")
    evidence: list[dict] = Field(
        default_factory=list,
        description="Per-criterion score evidence for transparency.",
    )
    availability: list[str] = Field(
        default_factory=list,
        description="Available interview slots.",
    )
    proposed_slot: str = Field(default="", description="Proposed interview slot.")
    guardrail_flags: list[str] = Field(
        default_factory=list,
        description="All guardrail flag identifiers accumulated during the run.",
    )
    fairness_flags: list[str] = Field(
        default_factory=list,
        description="Fairness-specific flag identifiers.",
    )
    approval_required: bool = Field(
        default=True,
        description="Always True — human approval is required before any interview action.",
    )
    status: str = Field(
        default="PENDING_APPROVAL",
        description="Current workflow status.",
    )
