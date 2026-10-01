"""LangGraph recruitment agent — graph definition.

Architecture
------------

    START
      │
      ▼
  parse_resume          ← calls parse_resume_service(resume_text, llm)
      │
      ▼
  score_candidate       ← calls score_candidate_service(profile, rubric, llm)
      │
      ▼
  route_on_score  ─── score < threshold ──► END  (REJECTED_BY_THRESHOLD)
      │
      ▼ (score ≥ threshold)
  check_availability    ← calls check_availability_service(name, week)
      │
      ▼
  propose_interview     ← calls propose_interview_service(name, slot)
      │
      ▼
  human_approval_gate   ← interrupt() — pauses for human input
      │
      ├── "approve" ──► finalise_approval  ──► END  (APPROVED)
      │
      └── "reject"  ──► finalise_rejection ──► END  (REJECTED)

Design notes
------------
- ``build_graph()`` accepts injectable service callables so tests can pass
  fake implementations without touching LangGraph internals.
- Every node catches exceptions, writes to ``state["errors"]``, and sets
  ``final_status = "FAILED"`` rather than propagating an unhandled exception.
- ``current_step`` is incremented on every node entry and checked against
  ``agent_max_iterations``.  If exceeded the graph sets FAILED and routes to END.
- The score threshold is passed into ``build_graph()`` from the caller (defaults
  to ``Settings.score_threshold`` if provided, otherwise 3.0).
- Pydantic models are serialised as dicts in state so MemorySaver can checkpoint
  them without a custom codec.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.state import RecruitmentState, TrajectoryEntry
from app.models import CandidateProfile, CandidateScore, ScoringCriterion
from app.tools.availability_tool import AvailabilityResult, check_availability_service
from app.tools.interview_tool import InterviewProposal, propose_interview_service
from app.tools.resume_tool import parse_resume_service
from app.tools.scoring_tool import score_candidate_service

# ── Type alias for injectable services ───────────────────────────────────────

ParseFn = Callable[[str, Any], CandidateProfile]          # (resume_text, llm) -> profile
ScoreFn = Callable[[CandidateProfile, list, Any], CandidateScore]  # (profile, rubric, llm) -> score
AvailFn = Callable[[str, str], AvailabilityResult]         # (candidate, week) -> slots
ProposeFn = Callable[[str, str], InterviewProposal]        # (candidate, slot) -> proposal

# Default week used when the caller does not specify one.
_DEFAULT_WEEK = "2026-W41"

# ── Helpers ───────────────────────────────────────────────────────────────────


def _step(state: RecruitmentState) -> int:
    return state.get("current_step", 0) + 1


def _tentry(step: int, node: str, status: str, detail: str = "") -> TrajectoryEntry:
    entry: TrajectoryEntry = {"step": step, "node": node, "status": status}
    if detail:
        entry["detail"] = detail
    return entry


def _guard_iterations(
    state: RecruitmentState, node: str, max_iter: int
) -> tuple[bool, dict]:
    """Return (exceeded, partial_state_update) if iteration limit is hit."""
    step = _step(state)
    if step > max_iter:
        return True, {
            "current_step": step,
            "final_status": "FAILED",
            "errors": [f"Iteration limit ({max_iter}) exceeded at node '{node}'."],
            "trajectory": [_tentry(step, node, "error", f"max iterations={max_iter} exceeded")],
        }
    return False, {}


# ── Node factories ────────────────────────────────────────────────────────────


def _make_parse_node(
    parse_fn: ParseFn,
    llm: Any,
    max_iter: int,
) -> Callable[[RecruitmentState], dict]:
    def parse_node(state: RecruitmentState) -> dict:
        step = _step(state)
        exceeded, upd = _guard_iterations(state, "parse_resume", max_iter)
        if exceeded:
            return upd

        traj: list[TrajectoryEntry] = [_tentry(step, "parse_resume", "entered")]
        try:
            resume_text = state.get("resume_text", "")
            profile: CandidateProfile = parse_fn(resume_text, llm)
            traj.append(_tentry(step, "parse_resume", "completed", f"name={profile.name}"))
            return {
                "current_step": step,
                "candidate_profile": profile.model_dump(),
                "final_status": "PARSED",
                "trajectory": traj,
            }
        except Exception as exc:  # noqa: BLE001
            msg = f"parse_resume failed: {type(exc).__name__}"
            traj.append(_tentry(step, "parse_resume", "error", msg))
            return {
                "current_step": step,
                "final_status": "FAILED",
                "errors": [msg],
                "trajectory": traj,
            }

    return parse_node


def _make_score_node(
    score_fn: ScoreFn,
    llm: Any,
    max_iter: int,
) -> Callable[[RecruitmentState], dict]:
    def score_node(state: RecruitmentState) -> dict:
        step = _step(state)
        exceeded, upd = _guard_iterations(state, "score_candidate", max_iter)
        if exceeded:
            return upd

        traj: list[TrajectoryEntry] = [_tentry(step, "score_candidate", "entered")]
        try:
            profile_dict = state.get("candidate_profile")
            if not profile_dict:
                raise ValueError("candidate_profile is missing from state")
            profile = CandidateProfile.model_validate(profile_dict)

            rubric_dicts = state.get("rubric", [])
            if not rubric_dicts:
                raise ValueError("rubric is missing from state")
            rubric = [ScoringCriterion.model_validate(r) for r in rubric_dicts]

            candidate_score: CandidateScore = score_fn(profile, rubric, llm)
            detail = (
                f"weighted_score={candidate_score.weighted_score}, "
                f"recommendation={candidate_score.recommendation}"
            )
            traj.append(_tentry(step, "score_candidate", "completed", detail))
            return {
                "current_step": step,
                "candidate_score": candidate_score.model_dump(),
                "final_status": "SCORED",
                "trajectory": traj,
            }
        except Exception as exc:  # noqa: BLE001
            msg = f"score_candidate failed: {type(exc).__name__}"
            traj.append(_tentry(step, "score_candidate", "error", msg))
            return {
                "current_step": step,
                "final_status": "FAILED",
                "errors": [msg],
                "trajectory": traj,
            }

    return score_node


def _make_availability_node(
    avail_fn: AvailFn,
    max_iter: int,
) -> Callable[[RecruitmentState], dict]:
    def availability_node(state: RecruitmentState) -> dict:
        step = _step(state)
        exceeded, upd = _guard_iterations(state, "check_availability", max_iter)
        if exceeded:
            return upd

        traj: list[TrajectoryEntry] = [_tentry(step, "check_availability", "entered")]
        try:
            profile_dict = state.get("candidate_profile", {})
            candidate_name = profile_dict.get("name", "Unknown")
            week = state.get("interview_week") or _DEFAULT_WEEK

            avail: AvailabilityResult = avail_fn(candidate_name, week)
            detail = f"candidate={avail.candidate}, week={avail.week}, slots={len(avail.available_slots)}"
            traj.append(_tentry(step, "check_availability", "completed", detail))
            return {
                "current_step": step,
                "availability": avail.model_dump(),
                "final_status": "AVAILABILITY_CHECKED",
                "trajectory": traj,
            }
        except Exception as exc:  # noqa: BLE001
            msg = f"check_availability failed: {type(exc).__name__}"
            traj.append(_tentry(step, "check_availability", "error", msg))
            return {
                "current_step": step,
                "final_status": "FAILED",
                "errors": [msg],
                "trajectory": traj,
            }

    return availability_node


def _make_propose_node(
    propose_fn: ProposeFn,
    max_iter: int,
) -> Callable[[RecruitmentState], dict]:
    def propose_node(state: RecruitmentState) -> dict:
        step = _step(state)
        exceeded, upd = _guard_iterations(state, "propose_interview", max_iter)
        if exceeded:
            return upd

        traj: list[TrajectoryEntry] = [_tentry(step, "propose_interview", "entered")]
        try:
            avail_dict = state.get("availability", {})
            slots: list[str] = avail_dict.get("available_slots", [])
            if not slots:
                raise ValueError("No available slots in state")

            profile_dict = state.get("candidate_profile", {})
            candidate_name = profile_dict.get("name", "Unknown")
            slot = slots[0]  # take the first available slot

            proposal: InterviewProposal = propose_fn(candidate_name, slot)
            detail = f"candidate={proposal.candidate}, slot={proposal.slot}"
            traj.append(_tentry(step, "propose_interview", "completed", detail))
            return {
                "current_step": step,
                "interview_proposal": proposal.model_dump(),
                "human_approval_required": True,
                "final_status": "PENDING_APPROVAL",
                "trajectory": traj,
            }
        except Exception as exc:  # noqa: BLE001
            msg = f"propose_interview failed: {type(exc).__name__}"
            traj.append(_tentry(step, "propose_interview", "error", msg))
            return {
                "current_step": step,
                "final_status": "FAILED",
                "errors": [msg],
                "trajectory": traj,
            }

    return propose_node


def _make_approval_node(max_iter: int) -> Callable[[RecruitmentState], dict]:
    def approval_node(state: RecruitmentState) -> dict:
        step = _step(state)
        exceeded, upd = _guard_iterations(state, "human_approval_gate", max_iter)
        if exceeded:
            return upd

        traj: list[TrajectoryEntry] = [_tentry(step, "human_approval_gate", "interrupted")]

        # interrupt() pauses the graph here.
        # On first execution it raises NodeInterrupt (caught by LangGraph).
        # On resume, graph.invoke(Command(resume=value)) continues from this
        # point with `decision` set to whatever the human passed.
        decision: str = interrupt(
            {
                "message": "Human approval required before interview is confirmed.",
                "proposal": state.get("interview_proposal", {}),
            }
        )

        traj.append(
            _tentry(step, "human_approval_gate", "resumed", f"decision={decision!r}")
        )

        if decision == "approve":
            return {
                "current_step": step,
                "human_approved": True,
                "trajectory": traj,
            }
        else:
            return {
                "current_step": step,
                "human_approved": False,
                "trajectory": traj,
            }

    return approval_node


def _make_finalise_approval_node(max_iter: int) -> Callable[[RecruitmentState], dict]:
    def finalise_approval(state: RecruitmentState) -> dict:
        step = _step(state)
        exceeded, upd = _guard_iterations(state, "finalise_approval", max_iter)
        if exceeded:
            return upd

        traj: list[TrajectoryEntry] = [
            _tentry(step, "finalise_approval", "completed", "interview approved")
        ]
        return {
            "current_step": step,
            "final_status": "APPROVED",
            "trajectory": traj,
        }

    return finalise_approval


def _make_finalise_rejection_node(max_iter: int) -> Callable[[RecruitmentState], dict]:
    def finalise_rejection(state: RecruitmentState) -> dict:
        step = _step(state)
        exceeded, upd = _guard_iterations(state, "finalise_rejection", max_iter)
        if exceeded:
            return upd

        traj: list[TrajectoryEntry] = [
            _tentry(step, "finalise_rejection", "completed", "interview rejected by human")
        ]
        return {
            "current_step": step,
            "final_status": "REJECTED",
            "trajectory": traj,
        }

    return finalise_rejection


# ── Routing functions ─────────────────────────────────────────────────────────


def _make_score_router(threshold: float) -> Callable[[RecruitmentState], str]:
    """Return a router function that uses ``threshold`` — defined in Python, not by the LLM."""

    def route_on_score(state: RecruitmentState) -> str:
        """Route to END if the score is below threshold or if an error occurred."""
        if state.get("final_status") == "FAILED":
            return END

        score_dict = state.get("candidate_score", {})
        weighted_score: float = score_dict.get("weighted_score", 0.0)

        if weighted_score < threshold:
            # Write the threshold-rejection status directly (can't return state from a router,
            # so we note it via a dedicated node instead; the router just picks the route).
            return "reject_by_threshold"
        return "check_availability"

    return route_on_score


def _make_approval_router() -> Callable[[RecruitmentState], str]:
    def route_on_approval(state: RecruitmentState) -> str:
        if state.get("final_status") == "FAILED":
            return END
        if state.get("human_approved"):
            return "finalise_approval"
        return "finalise_rejection"

    return route_on_approval


# ── Threshold-rejection node ──────────────────────────────────────────────────


def _make_reject_threshold_node(
    threshold: float, max_iter: int
) -> Callable[[RecruitmentState], dict]:
    def reject_threshold(state: RecruitmentState) -> dict:
        step = _step(state)
        exceeded, upd = _guard_iterations(state, "reject_by_threshold", max_iter)
        if exceeded:
            return upd

        score_dict = state.get("candidate_score", {})
        weighted = score_dict.get("weighted_score", 0.0)
        detail = f"score={weighted} < threshold={threshold}"
        traj: list[TrajectoryEntry] = [
            _tentry(step, "reject_by_threshold", "completed", detail)
        ]
        return {
            "current_step": step,
            "final_status": "REJECTED_BY_THRESHOLD",
            "trajectory": traj,
        }

    return reject_threshold


# ── Graph factory ─────────────────────────────────────────────────────────────


def build_graph(
    *,
    parse_fn: ParseFn | None = None,
    score_fn: ScoreFn | None = None,
    avail_fn: AvailFn | None = None,
    propose_fn: ProposeFn | None = None,
    llm: Any = None,
    score_threshold: float = 3.0,
    max_iterations: int = 10,
    checkpointer: Any = None,
):
    """Build and compile the LangGraph recruitment workflow.

    All service functions are injectable so tests can pass fakes without
    touching LangGraph internals.

    Args:
        parse_fn:        Replaces ``parse_resume_service`` in the parse node.
        score_fn:        Replaces ``score_candidate_service`` in the score node.
        avail_fn:        Replaces ``check_availability_service`` in the availability node.
        propose_fn:      Replaces ``propose_interview_service`` in the propose node.
        llm:             LLMService instance forwarded to parse_fn and score_fn.
        score_threshold: Candidates with weighted_score < threshold are rejected.
                         Must be in [0, 5].  Defaults to 3.0.
        max_iterations:  Hard cap on node executions.  Defaults to 10.
        checkpointer:    LangGraph checkpointer.  Defaults to MemorySaver().

    Returns:
        A compiled LangGraph ``CompiledGraph`` ready for ``invoke()``.
    """
    _parse_fn = parse_fn or parse_resume_service
    _score_fn = score_fn or score_candidate_service
    _avail_fn = avail_fn or check_availability_service
    _propose_fn = propose_fn or propose_interview_service

    # Build nodes using factory functions that close over injectable dependencies.
    parse_node = _make_parse_node(_parse_fn, llm, max_iterations)
    score_node = _make_score_node(_score_fn, llm, max_iterations)
    avail_node = _make_availability_node(_avail_fn, max_iterations)
    propose_node = _make_propose_node(_propose_fn, max_iterations)
    approval_node = _make_approval_node(max_iterations)
    finalise_approval = _make_finalise_approval_node(max_iterations)
    finalise_rejection = _make_finalise_rejection_node(max_iterations)
    reject_threshold = _make_reject_threshold_node(score_threshold, max_iterations)

    # Routing functions
    score_router = _make_score_router(score_threshold)
    approval_router = _make_approval_router()

    # Assemble the graph
    builder = StateGraph(RecruitmentState)

    builder.add_node("parse_resume", parse_node)
    builder.add_node("score_candidate", score_node)
    builder.add_node("reject_by_threshold", reject_threshold)
    builder.add_node("check_availability", avail_node)
    builder.add_node("propose_interview", propose_node)
    builder.add_node("human_approval_gate", approval_node)
    builder.add_node("finalise_approval", finalise_approval)
    builder.add_node("finalise_rejection", finalise_rejection)

    # Edges
    builder.add_edge(START, "parse_resume")
    builder.add_edge("parse_resume", "score_candidate")

    # Conditional: score threshold check
    builder.add_conditional_edges(
        "score_candidate",
        score_router,
        {
            "reject_by_threshold": "reject_by_threshold",
            "check_availability": "check_availability",
            END: END,
        },
    )

    builder.add_edge("reject_by_threshold", END)
    builder.add_edge("check_availability", "propose_interview")
    builder.add_edge("propose_interview", "human_approval_gate")

    # Conditional: human approval decision
    builder.add_conditional_edges(
        "human_approval_gate",
        approval_router,
        {
            "finalise_approval": "finalise_approval",
            "finalise_rejection": "finalise_rejection",
            END: END,
        },
    )

    builder.add_edge("finalise_approval", END)
    builder.add_edge("finalise_rejection", END)

    _checkpointer = checkpointer if checkpointer is not None else MemorySaver()
    return builder.compile(checkpointer=_checkpointer)
