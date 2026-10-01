"""High-level helpers for driving the recruitment agent graph.

These three functions form the human-facing API for Phase 4:

    run_recruitment(...)    — start a new recruitment run
    approve_interview(...)  — resume a paused run with approval
    reject_interview(...)   — resume a paused run with rejection

The functions accept an optional ``graph`` argument so tests can inject a
graph built with fake services.  In production the default compiled graph
(backed by MemorySaver) is used.

Thread IDs
----------
Each recruitment run needs a unique ``thread_id`` so the MemorySaver
checkpointer can isolate runs.  Callers supply the thread ID; a UUID is a
sensible default.  The same thread_id must be passed to ``approve_interview``
or ``reject_interview`` to resume the correct run.

Return value
------------
All three functions return the state dict from ``graph.invoke()``.  Callers
can inspect ``state["final_status"]`` and ``state["trajectory"]`` for results.
"""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.types import Command

from app.agent.graph import build_graph
from app.agent.state import RecruitmentState
from app.config import get_settings
from app.models import ScoringCriterion

# Module-level default graph (production path, no fake services).
# Built lazily on first use so importing this module never requires an API key.
_default_graph: Any = None


def _get_default_graph() -> Any:
    global _default_graph
    if _default_graph is None:
        settings = get_settings()
        _default_graph = build_graph(
            score_threshold=3.0,
            max_iterations=settings.agent_max_iterations,
        )
    return _default_graph


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


# ── Public helpers ────────────────────────────────────────────────────────────


def run_recruitment(
    resume_text: str,
    rubric: list[ScoringCriterion] | list[dict],
    *,
    job_description: str = "",
    interview_week: str = "2026-W41",
    thread_id: str | None = None,
    graph: Any = None,
) -> tuple[str, dict]:
    """Start a new recruitment run.

    Executes the graph until it either finishes (low score / error) or pauses
    at the ``human_approval_gate`` interrupt.

    Args:
        resume_text:      Raw resume text.
        rubric:           List of ``ScoringCriterion`` objects (or dicts) that
                          define the scoring criteria and weights.
        job_description:  Optional job description text (informational).
        interview_week:   ISO week string for slot lookup (default ``"2026-W41"``).
        thread_id:        Unique identifier for this run.  A UUID is generated
                          if not supplied.  **Keep it** — you need it to approve
                          or reject later.
        graph:            Compiled LangGraph graph.  Uses the default production
                          graph if not supplied.

    Returns:
        ``(thread_id, state)`` — the thread ID (needed for approval/rejection)
        and the state dict after the initial run.
    """
    if thread_id is None:
        thread_id = str(uuid.uuid4())

    # Normalise rubric to dicts for state serialisation.
    rubric_dicts: list[dict] = [
        r.model_dump() if isinstance(r, ScoringCriterion) else dict(r)
        for r in rubric
    ]

    initial_state: RecruitmentState = {
        "resume_text": resume_text,
        "job_description": job_description,
        "rubric": rubric_dicts,
        "interview_week": interview_week,
        "current_step": 0,
        "errors": [],
        "trajectory": [],
        "human_approval_required": False,
        "human_approved": False,
        "final_status": "STARTED",
        # Phase 5 additions
        "guardrail_flags": [],
        "fairness_flags": [],
    }

    _graph = graph or _get_default_graph()
    state = _graph.invoke(initial_state, config=_config(thread_id))
    return thread_id, state


def approve_interview(
    thread_id: str,
    *,
    graph: Any = None,
) -> dict:
    """Resume a paused recruitment run with human approval.

    The graph must be in ``PENDING_APPROVAL`` state (i.e. interrupted at
    ``human_approval_gate``).  Passing ``"approve"`` as the resume value
    causes the graph to route to ``finalise_approval`` and set
    ``final_status = "APPROVED"``.

    Args:
        thread_id: The thread ID returned by ``run_recruitment()``.
        graph:     Same compiled graph instance used for ``run_recruitment()``.

    Returns:
        Final state dict with ``final_status == "APPROVED"``.
    """
    _graph = graph or _get_default_graph()
    return _graph.invoke(Command(resume="approve"), config=_config(thread_id))


def reject_interview(
    thread_id: str,
    *,
    graph: Any = None,
) -> dict:
    """Resume a paused recruitment run with human rejection.

    Causes the graph to route to ``finalise_rejection`` and set
    ``final_status = "REJECTED"``.

    Args:
        thread_id: The thread ID returned by ``run_recruitment()``.
        graph:     Same compiled graph instance used for ``run_recruitment()``.

    Returns:
        Final state dict with ``final_status == "REJECTED"``.
    """
    _graph = graph or _get_default_graph()
    return _graph.invoke(Command(resume="reject"), config=_config(thread_id))
