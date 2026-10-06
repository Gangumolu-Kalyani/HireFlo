"""High-level helpers for driving the recruitment agent graph.

These three functions form the human-facing API for Phase 4:

    run_recruitment(...)    - start a new recruitment run
    approve_interview(...)  - resume a paused run with approval
    reject_interview(...)   - resume a paused run with rejection

The functions accept an optional ``graph`` argument so tests can inject a
graph built with fake services. In production the default compiled graph
(backed by MemorySaver) is used.

Thread IDs
----------
Each recruitment run needs a unique ``thread_id`` so the MemorySaver
checkpointer can isolate runs. Callers supply the thread ID; a UUID is a
sensible default. The same thread_id must be passed to ``approve_interview``
or ``reject_interview`` to resume the correct run.

Persistent audit logs
---------------------
The graph trajectory is also persisted through ``AuditStore`` when a
production database is configured. Only operational trajectory information
is stored. Raw resumes, job descriptions, prompts, model responses, and
secrets are not written to the persistent audit store.

Return value
------------
All three functions return the state dict from ``graph.invoke()``. Callers
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
from app.observability.audit_store import AuditStore
from app.observability.logging_config import get_logger

logger = get_logger(__name__)


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


def _persist_audit(
    thread_id: str,
    state: dict,
    *,
    trajectory_start: int = 0,
) -> None:
    """Persist safe operational trajectory entries.

    ``trajectory_start`` allows approval/rejection calls to persist only
    entries created after the graph resumes, preventing duplicate audit rows.

    Audit persistence is best-effort and must never make the recruitment
    workflow fail.
    """

    trajectory = state.get("trajectory", [])

    if not isinstance(trajectory, list):
        logger.error(
            "Audit persistence skipped | "
            "correlation_id=%s | reason=invalid_trajectory",
            thread_id,
        )
        return

    new_entries = trajectory[trajectory_start:]

    if not new_entries:
        return

    workflow_status = str(state.get("final_status", "UNKNOWN"))

    try:
        store = AuditStore()

        if not store.is_enabled():
            return

        if not store.ensure_schema():
            logger.error(
                "Audit persistence skipped | "
                "correlation_id=%s | reason=schema_unavailable",
                thread_id,
            )
            return

        store.persist_trajectory(
            correlation_id=thread_id,
            workflow_status=workflow_status,
            trajectory=new_entries,
        )

    except Exception as exc:
        logger.error(
            "Audit persistence failed unexpectedly | "
            "correlation_id=%s | error_type=%s",
            thread_id,
            type(exc).__name__,
        )


def _trajectory_length_before_resume(
    graph: Any,
    thread_id: str,
) -> int:
    """Return trajectory length from the current checkpoint.

    If checkpoint inspection is unavailable, return zero. This helper is used
    only to avoid duplicate persistent audit records when resuming a workflow.
    """

    try:
        snapshot = graph.get_state(_config(thread_id))
        values = getattr(snapshot, "values", {}) or {}
        trajectory = values.get("trajectory", [])

        if isinstance(trajectory, list):
            return len(trajectory)

    except Exception as exc:
        logger.warning(
            "Unable to inspect trajectory before resume | "
            "correlation_id=%s | error_type=%s",
            thread_id,
            type(exc).__name__,
        )

    return 0


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
    """

    if thread_id is None:
        thread_id = str(uuid.uuid4())

    logger.info(
        "Recruitment workflow started | correlation_id=%s",
        thread_id,
    )

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
        "guardrail_flags": [],
        "fairness_flags": [],
    }

    _graph = graph or _get_default_graph()

    state = _graph.invoke(
        initial_state,
        config=_config(thread_id),
    )

    status = state.get("final_status", "UNKNOWN")

    if status == "FAILED":
        logger.error(
            "Recruitment workflow failed | correlation_id=%s | errors=%s",
            thread_id,
            state.get("errors", []),
        )
    else:
        logger.info(
            "Recruitment workflow initial run completed | "
            "correlation_id=%s | status=%s",
            thread_id,
            status,
        )

    _persist_audit(
        thread_id,
        state,
    )

    return thread_id, state


def approve_interview(
    thread_id: str,
    *,
    graph: Any = None,
) -> dict:
    """Resume a paused recruitment run with human approval."""

    logger.info(
        "Interview approval received | correlation_id=%s",
        thread_id,
    )

    _graph = graph or _get_default_graph()

    trajectory_start = _trajectory_length_before_resume(
        _graph,
        thread_id,
    )

    state = _graph.invoke(
        Command(resume="approve"),
        config=_config(thread_id),
    )

    logger.info(
        "Interview approval workflow completed | "
        "correlation_id=%s | status=%s",
        thread_id,
        state.get("final_status", "UNKNOWN"),
    )

    _persist_audit(
        thread_id,
        state,
        trajectory_start=trajectory_start,
    )

    return state


def reject_interview(
    thread_id: str,
    *,
    graph: Any = None,
) -> dict:
    """Resume a paused recruitment run with human rejection."""

    logger.info(
        "Interview rejection received | correlation_id=%s",
        thread_id,
    )

    _graph = graph or _get_default_graph()

    trajectory_start = _trajectory_length_before_resume(
        _graph,
        thread_id,
    )

    state = _graph.invoke(
        Command(resume="reject"),
        config=_config(thread_id),
    )

    logger.info(
        "Interview rejection workflow completed | "
        "correlation_id=%s | status=%s",
        thread_id,
        state.get("final_status", "UNKNOWN"),
    )

    _persist_audit(
        thread_id,
        state,
        trajectory_start=trajectory_start,
    )

    return state
