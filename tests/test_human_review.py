"""Tests for human review and HumanReview model — Phase 5.

All tests run offline.

Tested scenarios
----------------
1. Interview proposal always requires approval
2. Missing approval prevents action (status remains PENDING_APPROVAL)
3. Rejection terminates workflow safely
4. Approval resumes correctly
5. HumanReview model validates correctly
6. HumanReview contains expected fields
7. approval_required is always True in HumanReview
8. Guardrail/fairness flags propagate to HumanReview
9. Proposal status remains PENDING_APPROVAL (no booking occurs)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agent.graph import build_graph
from app.agent.runner import approve_interview, reject_interview, run_recruitment
from app.agent.state import RecruitmentState
from app.models import CandidateProfile, CandidateScore, ScoreEvidence, ScoringCriterion
from app.models.guardrail_models import HumanReview
from app.tools.availability_tool import AvailabilityResult
from app.tools.interview_tool import InterviewProposal

# ── Shared fixtures ───────────────────────────────────────────────────────────

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


def _high_score() -> CandidateScore:
    return CandidateScore(
        candidate_name="Alex Rivera",
        scores=[
            ScoreEvidence(criterion="Python", score=5, evidence="5 years Python", reasoning="Expert"),
            ScoreEvidence(criterion="FastAPI", score=5, evidence="FastAPI expert", reasoning="Expert"),
        ],
        weighted_score=5.0,
        recommendation="strong_yes",
    )


def _avail(candidate: str, week: str) -> AvailabilityResult:
    return AvailabilityResult(
        candidate=candidate,
        week=week,
        available_slots=["Monday 10:00", "Tuesday 14:00"],
    )


def _propose(candidate: str, slot: str) -> InterviewProposal:
    return InterviewProposal(
        candidate=candidate,
        slot=slot,
        proposed_at=datetime.now(tz=timezone.utc).isoformat(),
    )


def _build(*, score: CandidateScore | None = None, checkpointer: Any = None):
    _score = score or _high_score()

    def _fake_parse(_text: str, _llm: Any) -> CandidateProfile:
        return ALEX

    def _fake_score(_profile: CandidateProfile, _rubric: list, _llm: Any) -> CandidateScore:
        return _score

    return build_graph(
        parse_fn=_fake_parse,
        score_fn=_fake_score,
        avail_fn=_avail,
        propose_fn=_propose,
        score_threshold=3.0,
        checkpointer=checkpointer or MemorySaver(),
    )


def _initial_state(resume: str = "Alex Rivera, Python developer.") -> RecruitmentState:
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
        "guardrail_flags": [],
        "fairness_flags": [],
    }


def _thread():
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


# ── Scenario 1: Proposal always requires approval ─────────────────────────────


class TestProposalRequiresApproval:
    def test_proposal_status_is_pending_approval(self):
        graph = _build()
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert state["interview_proposal"]["status"] == "PENDING_APPROVAL"

    def test_graph_pauses_before_booking(self):
        graph = _build()
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert "__interrupt__" in state

    def test_human_approval_required_is_set(self):
        graph = _build()
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert state.get("human_approval_required") is True

    def test_final_status_is_pending_before_decision(self):
        graph = _build()
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert state["final_status"] == "PENDING_APPROVAL"


# ── Scenario 2: Approval action prevented without approval ───────────────────


class TestNoAutoAction:
    def test_no_booking_before_approval(self):
        """After the initial run the proposal must still be PENDING_APPROVAL."""
        graph = _build()
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        # The graph is interrupted; no external action has occurred.
        proposal = state.get("interview_proposal", {})
        assert proposal.get("status") == "PENDING_APPROVAL"
        assert proposal.get("note") is not None  # note field confirms no action taken

    def test_no_email_sent_field_in_proposal(self):
        """The proposal note must state that no email has been sent."""
        graph = _build()
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        note = state["interview_proposal"].get("note", "")
        assert "email" in note.lower()

    def test_no_calendar_event_in_proposal(self):
        """The proposal note must state that no calendar event has been created."""
        graph = _build()
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        note = state["interview_proposal"].get("note", "")
        assert "calendar" in note.lower()


# ── Scenario 3: Rejection terminates workflow safely ─────────────────────────


class TestRejectionTerminates:
    def test_rejection_sets_final_status_rejected(self):
        memory = MemorySaver()
        graph = _build(checkpointer=memory)
        config = _thread()
        graph.invoke(_initial_state(), config=config)
        state = graph.invoke(Command(resume="reject"), config=config)
        assert state["final_status"] == "REJECTED"

    def test_rejection_does_not_book_interview(self):
        memory = MemorySaver()
        graph = _build(checkpointer=memory)
        config = _thread()
        graph.invoke(_initial_state(), config=config)
        state = graph.invoke(Command(resume="reject"), config=config)
        # Proposal must remain PENDING_APPROVAL — no booking happened.
        assert state["interview_proposal"]["status"] == "PENDING_APPROVAL"

    def test_rejection_sets_human_approved_false(self):
        memory = MemorySaver()
        graph = _build(checkpointer=memory)
        config = _thread()
        graph.invoke(_initial_state(), config=config)
        state = graph.invoke(Command(resume="reject"), config=config)
        assert state.get("human_approved") is False

    def test_rejection_does_not_raise(self):
        memory = MemorySaver()
        graph = _build(checkpointer=memory)
        config = _thread()
        graph.invoke(_initial_state(), config=config)
        # Must not raise.
        state = graph.invoke(Command(resume="reject"), config=config)
        assert "final_status" in state


# ── Scenario 4: Approval resumes correctly ───────────────────────────────────


class TestApprovalResumes:
    def test_approval_sets_final_status_approved(self):
        memory = MemorySaver()
        graph = _build(checkpointer=memory)
        config = _thread()
        graph.invoke(_initial_state(), config=config)
        state = graph.invoke(Command(resume="approve"), config=config)
        assert state["final_status"] == "APPROVED"

    def test_approval_sets_human_approved_true(self):
        memory = MemorySaver()
        graph = _build(checkpointer=memory)
        config = _thread()
        graph.invoke(_initial_state(), config=config)
        state = graph.invoke(Command(resume="approve"), config=config)
        assert state.get("human_approved") is True

    def test_approval_preserves_candidate_profile(self):
        memory = MemorySaver()
        graph = _build(checkpointer=memory)
        config = _thread()
        graph.invoke(_initial_state(), config=config)
        state = graph.invoke(Command(resume="approve"), config=config)
        assert state["candidate_profile"]["name"] == "Alex Rivera"

    def test_approval_no_longer_interrupted(self):
        memory = MemorySaver()
        graph = _build(checkpointer=memory)
        config = _thread()
        graph.invoke(_initial_state(), config=config)
        state = graph.invoke(Command(resume="approve"), config=config)
        assert "__interrupt__" not in state

    def test_runner_approve_helper_works(self):
        memory = MemorySaver()
        graph = _build(checkpointer=memory)
        thread_id, state1 = run_recruitment(
            "Alex Rivera, Python developer.",
            RUBRIC,
            thread_id=str(uuid.uuid4()),
            graph=graph,
        )
        assert "__interrupt__" in state1
        state2 = approve_interview(thread_id, graph=graph)
        assert state2["final_status"] == "APPROVED"

    def test_runner_reject_helper_works(self):
        memory = MemorySaver()
        graph = _build(checkpointer=memory)
        thread_id, _ = run_recruitment(
            "Alex Rivera, Python developer.",
            RUBRIC,
            thread_id=str(uuid.uuid4()),
            graph=graph,
        )
        state = reject_interview(thread_id, graph=graph)
        assert state["final_status"] == "REJECTED"


# ── Scenario 5-6: HumanReview model ──────────────────────────────────────────


class TestHumanReviewModel:
    def test_human_review_validates(self):
        review = HumanReview(
            candidate="Alex Rivera",
            score=4.5,
            recommendation="strong_yes",
            evidence=[{"criterion": "Python", "score": 4.5, "evidence": "5 years"}],
            availability=["Monday 10:00"],
            proposed_slot="Monday 10:00",
            guardrail_flags=[],
            fairness_flags=[],
            approval_required=True,
            status="PENDING_APPROVAL",
        )
        assert review.candidate == "Alex Rivera"
        assert review.score == 4.5
        assert review.approval_required is True

    def test_approval_required_always_true(self):
        review = HumanReview(
            candidate="Alex Rivera",
            score=4.5,
            recommendation="strong_yes",
        )
        assert review.approval_required is True

    def test_human_review_in_state_after_propose(self):
        """The graph must populate human_review in state before the interrupt."""
        graph = _build()
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        # human_review should be present after the build_human_review node.
        assert "human_review" in state
        assert state["human_review"]["candidate"] == "Alex Rivera"

    def test_human_review_contains_score(self):
        graph = _build()
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert state["human_review"]["score"] == pytest.approx(5.0)

    def test_human_review_contains_evidence(self):
        graph = _build()
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert len(state["human_review"]["evidence"]) > 0

    def test_human_review_contains_proposed_slot(self):
        graph = _build()
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert state["human_review"]["proposed_slot"] != ""

    def test_human_review_approval_required_true(self):
        graph = _build()
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        assert state["human_review"]["approval_required"] is True


# ── Scenario 8: Guardrail flags propagate ────────────────────────────────────


class TestGuardrailFlagsInHumanReview:
    def test_human_review_includes_guardrail_flags_field(self):
        graph = _build()
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        review = state["human_review"]
        assert "guardrail_flags" in review
        assert isinstance(review["guardrail_flags"], list)

    def test_human_review_includes_fairness_flags_field(self):
        graph = _build()
        config = _thread()
        state = graph.invoke(_initial_state(), config=config)
        review = state["human_review"]
        assert "fairness_flags" in review
        assert isinstance(review["fairness_flags"], list)
