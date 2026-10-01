"""Tests for the Phase 4 LangGraph recruitment agent.

Every test uses injected fake service functions — no OpenRouter calls are made.
All tests run offline and are deterministic.

Tested scenarios
----------------
1. High-scoring candidate  → parse → score → availability → proposal → interrupt
2. Low-scoring candidate   → parse → score → threshold rejection → END
3. Human approval          → initial run (interrupt) → approve → APPROVED
4. Human rejection         → initial run (interrupt) → reject  → REJECTED
5. Tool failure            → simulated parse failure → FAILED state
6. Iteration limit         → max_iterations=1 → FAILED before completing
7. Trajectory              → audit entries are recorded at each node
8. Deterministic routing   → same score always produces same routing decision
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agent.graph import build_graph
from app.agent.runner import approve_interview, reject_interview, run_recruitment
from app.agent.state import RecruitmentState
from app.models import CandidateProfile, CandidateScore, ScoreEvidence, ScoringCriterion
from app.tools.availability_tool import AvailabilityResult
from app.tools.interview_tool import InterviewProposal

# ── Fixtures / fake services ──────────────────────────────────────────────────

ALEX = CandidateProfile(
    name="Alex Rivera",
    email="alex@example.com",
    skills=["Python", "FastAPI"],
    years_of_experience=5,
)

RUBRIC = [
    ScoringCriterion(name="Python", weight=0.5),
    ScoringCriterion(name="FastAPI", weight=0.5),
]

RUBRIC_DICTS = [r.model_dump() for r in RUBRIC]


def _high_score(name: str = "Alex Rivera") -> CandidateScore:
    return CandidateScore(
        candidate_name=name,
        scores=[
            ScoreEvidence(criterion="Python", score=5, evidence="5 years Python"),
            ScoreEvidence(criterion="FastAPI", score=5, evidence="FastAPI expert"),
        ],
        weighted_score=5.0,
        recommendation="strong_yes",
    )


def _low_score(name: str = "Alex Rivera") -> CandidateScore:
    return CandidateScore(
        candidate_name=name,
        scores=[
            ScoreEvidence(criterion="Python", score=1, evidence="Basic Python only"),
            ScoreEvidence(criterion="FastAPI", score=1, evidence="No FastAPI experience"),
        ],
        weighted_score=1.0,
        recommendation="no",
    )


def _avail(candidate: str, week: str) -> AvailabilityResult:
    return AvailabilityResult(
        candidate=candidate,
        week=week,
        available_slots=["Monday 10:00", "Tuesday 14:00"],
    )


def _propose(candidate: str, slot: str) -> InterviewProposal:
    from datetime import datetime, timezone
    return InterviewProposal(
        candidate=candidate,
        slot=slot,
        proposed_at=datetime.now(tz=timezone.utc).isoformat(),
    )


def _fake_parse(_resume_text: str, _llm: Any) -> CandidateProfile:
    return ALEX


def _failing_parse(_resume_text: str, _llm: Any) -> CandidateProfile:
    raise RuntimeError("LLM service unavailable")


def _make_score_fn(score: CandidateScore):
    def _fake_score(_profile: CandidateProfile, _rubric: list, _llm: Any) -> CandidateScore:
        return score
    return _fake_score


def _build(
    *,
    score: CandidateScore | None = None,
    parse_fn=None,
    score_threshold: float = 3.0,
    max_iterations: int = 10,
    checkpointer: Any = None,
):
    """Build a test graph with fake services."""
    _parse = parse_fn or _fake_parse
    _score = _make_score_fn(score or _high_score())
    _checkpointer = checkpointer or MemorySaver()
    return build_graph(
        parse_fn=_parse,
        score_fn=_score,
        avail_fn=_avail,
        propose_fn=_propose,
        score_threshold=score_threshold,
        max_iterations=max_iterations,
        checkpointer=_checkpointer,
    )


def _initial_state(resume: str = "dummy resume text") -> RecruitmentState:
    return {
        "resume_text": resume,
        "job_description": "Python backend role",
        "rubric": RUBRIC_DICTS,
        "interview_week": "2026-W41",
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


def _thread() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


# ── Scenario 1: High-scoring candidate ───────────────────────────────────────


class TestHighScoringCandidate:
    """High-scoring candidate: graph runs to the interrupt gate."""

    def test_graph_pauses_at_human_approval(self):
        graph = _build(score=_high_score())
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)

        # Graph should have paused — __interrupt__ key present.
        assert "__interrupt__" in state, "Expected interrupt, got none"

    def test_final_status_is_pending_approval_before_interrupt(self):
        graph = _build(score=_high_score())
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert state["final_status"] == "PENDING_APPROVAL"

    def test_interview_proposal_is_present(self):
        graph = _build(score=_high_score())
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert "interview_proposal" in state
        assert state["interview_proposal"]["status"] == "PENDING_APPROVAL"

    def test_availability_is_present(self):
        graph = _build(score=_high_score())
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert "availability" in state
        assert len(state["availability"]["available_slots"]) > 0

    def test_candidate_profile_is_present(self):
        graph = _build(score=_high_score())
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert state["candidate_profile"]["name"] == "Alex Rivera"

    def test_no_errors(self):
        graph = _build(score=_high_score())
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert state.get("errors", []) == []


# ── Scenario 2: Low-scoring candidate ────────────────────────────────────────


class TestLowScoringCandidate:
    """Low-scoring candidate: graph terminates with REJECTED_BY_THRESHOLD."""

    def test_final_status_is_rejected_by_threshold(self):
        graph = _build(score=_low_score(), score_threshold=3.0)
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert state["final_status"] == "REJECTED_BY_THRESHOLD"

    def test_no_interrupt_for_low_score(self):
        graph = _build(score=_low_score(), score_threshold=3.0)
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert "__interrupt__" not in state

    def test_no_interview_proposal_for_low_score(self):
        graph = _build(score=_low_score(), score_threshold=3.0)
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert "interview_proposal" not in state or not state.get("interview_proposal")

    def test_candidate_score_is_recorded(self):
        graph = _build(score=_low_score(), score_threshold=3.0)
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert state["candidate_score"]["weighted_score"] == pytest.approx(1.0)

    def test_exactly_at_threshold_passes(self):
        """Score exactly equal to threshold should pass (threshold is exclusive lower bound)."""
        at_threshold = CandidateScore(
            candidate_name="Alex Rivera",
            scores=[ScoreEvidence(criterion="Python", score=3, evidence="ok")],
            weighted_score=3.0,
            recommendation="maybe",
        )
        graph = _build(score=at_threshold, score_threshold=3.0)
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        # Score == threshold should still proceed to availability (not rejected).
        assert state["final_status"] != "REJECTED_BY_THRESHOLD"

    def test_score_just_below_threshold_is_rejected(self):
        below = CandidateScore(
            candidate_name="Alex Rivera",
            scores=[ScoreEvidence(criterion="Python", score=2.9, evidence="below")],
            weighted_score=2.9,
            recommendation="no",
        )
        graph = _build(score=below, score_threshold=3.0)
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert state["final_status"] == "REJECTED_BY_THRESHOLD"


# ── Scenario 3: Human approval ────────────────────────────────────────────────


class TestHumanApproval:
    """Full approval flow: interrupt → resume with 'approve' → APPROVED."""

    def test_approval_sets_final_status_approved(self):
        memory = MemorySaver()
        graph = _build(score=_high_score(), checkpointer=memory)
        config = _thread()

        # First invoke — pauses at interrupt.
        state1 = graph.invoke(_initial_state(), config=config)
        assert "__interrupt__" in state1

        # Resume with approval.
        state2 = graph.invoke(Command(resume="approve"), config=config)
        assert state2["final_status"] == "APPROVED"

    def test_approval_sets_human_approved_true(self):
        memory = MemorySaver()
        graph = _build(score=_high_score(), checkpointer=memory)
        config = _thread()
        graph.invoke(_initial_state(), config=config)
        state = graph.invoke(Command(resume="approve"), config=config)
        assert state.get("human_approved") is True

    def test_approval_no_longer_interrupted(self):
        memory = MemorySaver()
        graph = _build(score=_high_score(), checkpointer=memory)
        config = _thread()
        graph.invoke(_initial_state(), config=config)
        state = graph.invoke(Command(resume="approve"), config=config)
        assert "__interrupt__" not in state

    def test_approval_preserves_interview_proposal(self):
        memory = MemorySaver()
        graph = _build(score=_high_score(), checkpointer=memory)
        config = _thread()
        graph.invoke(_initial_state(), config=config)
        state = graph.invoke(Command(resume="approve"), config=config)
        assert state["interview_proposal"]["candidate"] == "Alex Rivera"

    def test_run_recruitment_and_approve_helpers(self):
        """End-to-end via the runner helper functions."""
        memory = MemorySaver()
        graph = _build(score=_high_score(), checkpointer=memory)

        thread_id, state1 = run_recruitment(
            "resume text",
            RUBRIC,
            thread_id=str(uuid.uuid4()),
            graph=graph,
        )
        assert "__interrupt__" in state1

        state2 = approve_interview(thread_id, graph=graph)
        assert state2["final_status"] == "APPROVED"


# ── Scenario 4: Human rejection ───────────────────────────────────────────────


class TestHumanRejection:
    """Full rejection flow: interrupt → resume with 'reject' → REJECTED."""

    def test_rejection_sets_final_status_rejected(self):
        memory = MemorySaver()
        graph = _build(score=_high_score(), checkpointer=memory)
        config = _thread()

        graph.invoke(_initial_state(), config=config)
        state = graph.invoke(Command(resume="reject"), config=config)
        assert state["final_status"] == "REJECTED"

    def test_rejection_sets_human_approved_false(self):
        memory = MemorySaver()
        graph = _build(score=_high_score(), checkpointer=memory)
        config = _thread()
        graph.invoke(_initial_state(), config=config)
        state = graph.invoke(Command(resume="reject"), config=config)
        assert state.get("human_approved") is False

    def test_reject_helper(self):
        memory = MemorySaver()
        graph = _build(score=_high_score(), checkpointer=memory)

        thread_id, _ = run_recruitment(
            "resume text", RUBRIC, thread_id=str(uuid.uuid4()), graph=graph
        )
        state = reject_interview(thread_id, graph=graph)
        assert state["final_status"] == "REJECTED"

    def test_rejection_does_not_book_anything(self):
        """Rejection must not send emails or create calendar events.
        The proposal stays PENDING_APPROVAL in the state — it is never 'booked'."""
        memory = MemorySaver()
        graph = _build(score=_high_score(), checkpointer=memory)
        config = _thread()
        graph.invoke(_initial_state(), config=config)
        state = graph.invoke(Command(resume="reject"), config=config)
        # Proposal still shows PENDING_APPROVAL — no external booking happened.
        assert state["interview_proposal"]["status"] == "PENDING_APPROVAL"


# ── Scenario 5: Tool failure ──────────────────────────────────────────────────


class TestToolFailure:
    """A service failure should produce FAILED state, not an unhandled exception."""

    def test_parse_failure_produces_failed_status(self):
        graph = _build(parse_fn=_failing_parse)
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert state["final_status"] == "FAILED"

    def test_parse_failure_records_error(self):
        graph = _build(parse_fn=_failing_parse)
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert len(state.get("errors", [])) > 0

    def test_parse_failure_does_not_expose_secrets(self):
        graph = _build(parse_fn=_failing_parse)
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        # Error messages should only contain the exception type name, not internals.
        for err in state.get("errors", []):
            assert "sk-or-" not in err
            assert "password" not in err.lower()

    def test_score_failure_produces_failed_status(self):
        def _failing_score(_profile, _rubric, _llm):
            raise RuntimeError("scoring service unavailable")

        graph = build_graph(
            parse_fn=_fake_parse,
            score_fn=_failing_score,
            avail_fn=_avail,
            propose_fn=_propose,
        )
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert state["final_status"] == "FAILED"
        assert len(state.get("errors", [])) > 0

    def test_failure_does_not_raise_exception(self):
        """The graph must not let exceptions propagate to the caller."""
        graph = _build(parse_fn=_failing_parse)
        config = _thread()
        # Should complete without raising.
        state = graph.invoke(_initial_state(), config=config)
        assert "final_status" in state


# ── Scenario 6: Iteration limit ───────────────────────────────────────────────


class TestIterationLimit:
    """When max_iterations is set very low, the graph should stop safely."""

    def test_iteration_limit_produces_failed_status(self):
        # max_iterations=1 means the second node invocation will exceed the cap.
        graph = _build(score=_high_score(), max_iterations=1)
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert state["final_status"] == "FAILED"

    def test_iteration_limit_records_error(self):
        graph = _build(score=_high_score(), max_iterations=1)
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        errors = state.get("errors", [])
        assert any("Iteration limit" in e for e in errors)

    def test_iteration_limit_does_not_raise(self):
        graph = _build(score=_high_score(), max_iterations=1)
        config = _thread()
        # Must not raise.
        state = graph.invoke(_initial_state(), config=config)
        assert "final_status" in state

    def test_normal_run_completes_within_default_limit(self):
        """A normal run should not hit the default 10-iteration limit."""
        graph = _build(score=_high_score(), max_iterations=10)
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        # Should reach interrupt, not FAILED.
        assert state["final_status"] == "PENDING_APPROVAL"


# ── Scenario 7: Trajectory ────────────────────────────────────────────────────


class TestTrajectory:
    """Audit entries must be recorded at each significant graph event."""

    def test_trajectory_is_a_list(self):
        graph = _build(score=_high_score())
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert isinstance(state.get("trajectory"), list)

    def test_trajectory_contains_parse_entry(self):
        graph = _build(score=_high_score())
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        nodes = [t["node"] for t in state["trajectory"]]
        assert "parse_resume" in nodes

    def test_trajectory_contains_score_entry(self):
        graph = _build(score=_high_score())
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        nodes = [t["node"] for t in state["trajectory"]]
        assert "score_candidate" in nodes

    def test_trajectory_contains_availability_entry(self):
        graph = _build(score=_high_score())
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        nodes = [t["node"] for t in state["trajectory"]]
        assert "check_availability" in nodes

    def test_trajectory_contains_propose_entry(self):
        graph = _build(score=_high_score())
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        nodes = [t["node"] for t in state["trajectory"]]
        assert "propose_interview" in nodes

    def test_trajectory_contains_interrupt_entry(self):
        # The interrupt entry appears in trajectory AFTER resuming, because
        # human_approval_gate records its entries when it executes (which
        # happens on the resume invoke, not the initial invoke).
        memory = MemorySaver()
        graph = _build(score=_high_score(), checkpointer=memory)
        config = _thread()
        graph.invoke(_initial_state(), config=config)
        state = graph.invoke(Command(resume="approve"), config=config)
        statuses = [t["status"] for t in state["trajectory"]]
        assert "interrupted" in statuses

    def test_trajectory_grows_after_approval(self):
        memory = MemorySaver()
        graph = _build(score=_high_score(), checkpointer=memory)
        config = _thread()
        s1 = graph.invoke(_initial_state(), config=config)
        traj_before = len(s1["trajectory"])

        s2 = graph.invoke(Command(resume="approve"), config=config)
        assert len(s2["trajectory"]) > traj_before

    def test_trajectory_records_step_numbers(self):
        graph = _build(score=_high_score())
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        for entry in state["trajectory"]:
            assert "step" in entry
            assert entry["step"] >= 1

    def test_low_score_trajectory_contains_rejection_entry(self):
        graph = _build(score=_low_score(), score_threshold=3.0)
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        nodes = [t["node"] for t in state["trajectory"]]
        assert "reject_by_threshold" in nodes


# ── Scenario 8: Deterministic routing ────────────────────────────────────────


class TestDeterministicRouting:
    """The same score must always produce the same routing decision.
    Python (not the LLM) makes the routing decision."""

    def test_high_score_always_routes_to_availability(self):
        """Three runs with the same high score always hit the interrupt."""
        for _ in range(3):
            graph = _build(score=_high_score(), score_threshold=3.0)
            config = _thread()
            state = graph.invoke(_initial_state(), config=config)
            assert state["final_status"] == "PENDING_APPROVAL"

    def test_low_score_always_rejected(self):
        """Three runs with the same low score always end at threshold rejection."""
        for _ in range(3):
            graph = _build(score=_low_score(), score_threshold=3.0)
            config = _thread()
            state = graph.invoke(_initial_state(), config=config)
            assert state["final_status"] == "REJECTED_BY_THRESHOLD"

    def test_different_names_same_score_same_routing(self):
        """Changing the candidate name does not change the routing decision."""
        for name in ["Alex Rivera", "Jordan Lee", "Priya Sharma"]:
            score = _high_score(name)

            def _score_fn(_p, _r, _l, _s=score):
                return _s

            graph = build_graph(
                parse_fn=lambda t, l, n=name: CandidateProfile(name=n, skills=["Python"]),
                score_fn=_score_fn,
                avail_fn=_avail,
                propose_fn=_propose,
                score_threshold=3.0,
            )
            config = _thread()
            state = graph.invoke(_initial_state(), config=config)
            assert state["final_status"] == "PENDING_APPROVAL", f"Failed for name={name}"

    def test_threshold_boundary_is_exclusive_at_threshold(self):
        """Score = threshold should NOT be rejected (threshold is a minimum, not exclusive)."""
        exact = CandidateScore(
            candidate_name="Alex",
            scores=[ScoreEvidence(criterion="Python", score=3.0, evidence="ok")],
            weighted_score=3.0,
            recommendation="maybe",
        )
        graph = _build(score=exact, score_threshold=3.0)
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert state["final_status"] != "REJECTED_BY_THRESHOLD"

    def test_configurable_threshold_respected(self):
        """A high threshold (4.9) rejects a 5.0 scorer would be unreasonable,
        but a threshold of 5.1 should reject everything."""
        # Score = 5.0, threshold = 5.1 → should reject.
        high = _high_score()  # weighted_score=5.0
        graph = _build(score=high, score_threshold=5.1)
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert state["final_status"] == "REJECTED_BY_THRESHOLD"


# ── Integrity checks ──────────────────────────────────────────────────────────


class TestIntegrity:
    """Misc invariants that should hold across all scenarios."""

    def test_langgraph_is_actually_used(self):
        """The graph must be a LangGraph CompiledStateGraph, not a plain function."""
        from langgraph.graph.state import CompiledStateGraph
        graph = _build()
        assert isinstance(graph, CompiledStateGraph)

    def test_no_interview_booked_after_proposal(self):
        """The proposal status must remain PENDING_APPROVAL — no booking occurs."""
        graph = _build(score=_high_score())
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        proposal = state.get("interview_proposal", {})
        assert proposal.get("status") == "PENDING_APPROVAL"

    def test_no_interview_booked_after_approval(self):
        """Even after approval the proposal status is still PENDING_APPROVAL in state.
        Actual booking is out-of-scope until Phase 5."""
        memory = MemorySaver()
        graph = _build(score=_high_score(), checkpointer=memory)
        config = _thread()
        graph.invoke(_initial_state(), config=config)
        state = graph.invoke(Command(resume="approve"), config=config)
        # The graph marks final_status=APPROVED but the proposal object itself
        # is unchanged — no external booking happened.
        assert state["interview_proposal"]["status"] == "PENDING_APPROVAL"

    def test_errors_list_empty_on_clean_run(self):
        graph = _build(score=_high_score())
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert state.get("errors", []) == []

    def test_state_contains_all_expected_keys_after_high_score_run(self):
        graph = _build(score=_high_score())
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        for key in ("candidate_profile", "candidate_score", "availability",
                    "interview_proposal", "final_status", "trajectory"):
            assert key in state, f"Missing key: {key}"
