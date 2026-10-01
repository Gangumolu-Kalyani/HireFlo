"""Recruitment agent state definition.

The state is the single source of truth for the LangGraph workflow.
Every node reads from state and returns a partial update dict.

Design notes
------------
- ``TypedDict`` is used (not a dataclass or Pydantic model) because LangGraph's
  StateGraph expects a TypedDict schema.
- ``Annotated[list, operator.add]`` on ``trajectory`` and ``errors`` means
  LangGraph will *append* items returned by a node rather than replace the list.
  All other fields use last-write-wins semantics.
- Pydantic models are stored as dicts in state so LangGraph can serialise them
  through the MemorySaver checkpointer without custom codec logic.  The node
  implementations reconstruct Pydantic objects from those dicts as needed.
"""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

# ── Status literals ───────────────────────────────────────────────────────────

FinalStatus = Literal[
    "STARTED",
    "PARSED",
    "SCORED",
    "REJECTED_BY_THRESHOLD",
    "AVAILABILITY_CHECKED",
    "PENDING_APPROVAL",
    "APPROVED",
    "REJECTED",
    "FAILED",
]

# ── Trajectory entry ──────────────────────────────────────────────────────────


class TrajectoryEntry(TypedDict, total=False):
    """One audit record written by a node.

    Only operational / audit information is stored here — never chain-of-thought
    or private LLM reasoning.
    """

    step: int
    node: str
    status: str          # "entered" | "completed" | "error" | "interrupted"
    detail: str          # optional human-readable detail


# ── Main state ────────────────────────────────────────────────────────────────


class RecruitmentState(TypedDict, total=False):
    """Full state carried through the recruitment workflow.

    Fields
    ------
    resume_text
        Raw resume text provided by the caller.
    job_description
        Raw job description text (informational; used by the rubric builder).
    candidate_profile
        Dict-serialised ``CandidateProfile`` (set by the parse node).
    rubric
        List of dict-serialised ``ScoringCriterion`` objects provided by the caller.
    candidate_score
        Dict-serialised ``CandidateScore`` (set by the score node).
    availability
        Dict-serialised ``AvailabilityResult`` (set by the availability node).
    interview_proposal
        Dict-serialised ``InterviewProposal`` (set by the propose node).
    interview_week
        ISO week string for the interview (e.g. ``"2026-W41"``).
    current_step
        Monotonically increasing counter; checked against AGENT_MAX_ITERATIONS.
    errors
        Append-only list of error strings.  Each node appends on failure.
    human_approval_required
        Set to True when the graph reaches the approval gate.
    human_approved
        Set to True/False when the human responds.
    final_status
        Last known status — one of the ``FinalStatus`` literals.
    trajectory
        Append-only audit log of ``TrajectoryEntry`` dicts.
    """

    resume_text: str
    job_description: str
    candidate_profile: dict
    rubric: list[dict]
    candidate_score: dict
    availability: dict
    interview_proposal: dict
    interview_week: str
    current_step: int
    errors: Annotated[list[str], operator.add]
    human_approval_required: bool
    human_approved: bool
    final_status: FinalStatus
    trajectory: Annotated[list[TrajectoryEntry], operator.add]
