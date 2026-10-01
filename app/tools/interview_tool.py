"""propose_interview tool — creates a PENDING_APPROVAL interview proposal.

⚠️  This tool ONLY creates a proposal object.  It does NOT:
    - send emails
    - create calendar events
    - contact candidates or interviewers
    - make any external API calls

Human approval and actual booking are handled in a later phase (Phase 5).
The ``status`` field is always ``"PENDING_APPROVAL"`` until a human acts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

# ── Output model ──────────────────────────────────────────────────────────────


class InterviewProposal(BaseModel):
    """A proposed interview slot awaiting human approval.

    Status is always PENDING_APPROVAL at creation.  No booking has been made.
    """

    candidate: str = Field(min_length=1, description="Candidate name")
    slot: str = Field(min_length=1, description="Proposed interview slot, e.g. 'Tuesday 14:00'")
    status: Literal["PENDING_APPROVAL"] = Field(
        default="PENDING_APPROVAL",
        description="Always PENDING_APPROVAL — human approval required before booking.",
    )
    proposed_at: str = Field(
        description="ISO 8601 UTC timestamp when this proposal was created."
    )
    note: str = Field(
        default=(
            "This is a proposal only.  No email has been sent, no calendar event has been created,"
            " and the candidate has not been contacted.  Human approval is required."
        ),
        description="Reminder that no action has been taken.",
    )


# ── Service layer ─────────────────────────────────────────────────────────────


def propose_interview_service(candidate: str, slot: str) -> InterviewProposal:
    """Create an interview proposal with status PENDING_APPROVAL.

    This function does NOT send emails, create calendar events, or contact
    anyone.  It only produces a structured proposal that a human must approve.

    Args:
        candidate: The candidate's name (non-empty).
        slot:      The proposed interview slot (non-empty), e.g. ``"Tuesday 14:00"``.

    Returns:
        ``InterviewProposal`` with ``status == "PENDING_APPROVAL"``.

    Raises:
        ValueError: if ``candidate`` or ``slot`` is empty.
    """
    candidate = candidate.strip()
    if not candidate:
        raise ValueError("Candidate name must not be empty.")

    slot = slot.strip()
    if not slot:
        raise ValueError("Interview slot must not be empty.")

    return InterviewProposal(
        candidate=candidate,
        slot=slot,
        proposed_at=datetime.now(tz=timezone.utc).isoformat(),
    )


# ── @tool wrapper (used by LangGraph) ────────────────────────────────────────


@tool
def propose_interview(candidate: str, slot: str) -> dict:
    """Propose an interview slot for a candidate.

    ⚠️  Creates a PENDING_APPROVAL proposal ONLY.  No emails are sent, no
    calendar events are created, and the candidate is not contacted.
    Human approval is required before any booking is made (Phase 5).

    Args:
        candidate: The candidate's name.
        slot:      The proposed slot, e.g. ``"Tuesday 14:00"``.

    Returns:
        A dict with keys ``candidate``, ``slot``, ``status``
        (always ``"PENDING_APPROVAL"``), ``proposed_at``, and ``note``.

    Raises:
        ValueError: if ``candidate`` or ``slot`` is empty.
    """
    return propose_interview_service(candidate, slot).model_dump()
