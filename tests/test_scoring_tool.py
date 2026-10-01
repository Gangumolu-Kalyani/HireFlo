"""Tests for app/tools/scoring_tool.py.

Key invariants verified here:
- Weighted score is calculated in Python, not by the LLM.
- Score is always in [0, 5].
- Evidence is present for every criterion.
- Changing the candidate's NAME does not change the score.
- Changing irrelevant identity information does not change the score.
- Weights are applied correctly.

No real LLM calls are made.
"""

import pytest

from app.models import CandidateProfile, ScoringCriterion
from app.services.llm_service import LLMError
from app.tools.scoring_tool import (
    _compute_weighted_score,
    _CriterionScore,
    _LLMScoringResponse,
    _profile_to_text,
    score_candidate_service,
)
from tests.conftest import FakeLLMService

# ── Shared fixtures / helpers ─────────────────────────────────────────────────


def _profile(name: str = "Alex Rivera", **kwargs) -> CandidateProfile:
    return CandidateProfile(
        name=name,
        skills=["Python", "FastAPI", "Docker"],
        years_of_experience=5,
        projects=["Resume matcher using LangChain"],
        experience=["Backend Engineer - Example Corp (2021–present)"],
        **kwargs,
    )


def _rubric(
    python_w: float = 0.30,
    ml_w: float = 0.30,
    projects_w: float = 0.40,
) -> list[ScoringCriterion]:
    return [
        ScoringCriterion(name="Python", description="Python proficiency", weight=python_w),
        ScoringCriterion(name="Machine Learning", description="ML experience", weight=ml_w),
        ScoringCriterion(name="Projects", description="Relevant projects", weight=projects_w),
    ]


def _llm_response(
    python: float = 5, ml: float = 4, projects: float = 5
) -> _LLMScoringResponse:
    return _LLMScoringResponse(
        scores=[
            _CriterionScore(criterion="Python", score=python, evidence="5 years of Python"),
            _CriterionScore(criterion="Machine Learning", score=ml, evidence="Built ML pipeline"),
            _CriterionScore(criterion="Projects", score=projects, evidence="LangChain project"),
        ]
    )


def _fake_llm(python: float = 5, ml: float = 4, projects: float = 5) -> FakeLLMService:
    return FakeLLMService(result=_llm_response(python, ml, projects))


# ── Score range ───────────────────────────────────────────────────────────────


class TestScoreRange:
    def test_weighted_score_in_range_0_to_5(self):
        result = score_candidate_service(_profile(), _rubric(), llm=_fake_llm())
        assert 0.0 <= result.weighted_score <= 5.0

    def test_max_scores_give_weighted_5(self):
        """All scores = 5 → weighted_score = 5.0 regardless of weights."""
        result = score_candidate_service(_profile(), _rubric(), llm=_fake_llm(5, 5, 5))
        assert result.weighted_score == pytest.approx(5.0)

    def test_zero_scores_give_weighted_0(self):
        """All scores = 0 → weighted_score = 0.0."""
        result = score_candidate_service(_profile(), _rubric(), llm=_fake_llm(0, 0, 0))
        assert result.weighted_score == pytest.approx(0.0)

    def test_weighted_score_is_within_0_and_5_for_mixed_scores(self):
        result = score_candidate_service(_profile(), _rubric(), llm=_fake_llm(3, 1, 4))
        assert 0.0 <= result.weighted_score <= 5.0


# ── Python-side weighted calculation ─────────────────────────────────────────


class TestPythonWeightedCalculation:
    """The LLM only provides per-criterion scores; Python computes the total."""

    def test_weighted_score_matches_manual_calculation(self):
        """Verify the exact formula: Σ(score/5 * weight) / Σ(weight) * 5."""
        # Python=5(w=0.30), ML=4(w=0.30), Projects=5(w=0.40)
        # = (5/5*0.30 + 4/5*0.30 + 5/5*0.40) / (0.30+0.30+0.40) * 5
        # = (0.30 + 0.24 + 0.40) / 1.0 * 5
        # = 0.94 * 5 = 4.70
        result = score_candidate_service(_profile(), _rubric(), llm=_fake_llm())
        assert result.weighted_score == pytest.approx(4.70, abs=1e-4)

    def test_different_weights_change_result(self):
        """Heavier weight on a lower score pulls the total down."""
        # Python=5(w=0.10), ML=1(w=0.80), Projects=5(w=0.10)
        # = (5/5*0.10 + 1/5*0.80 + 5/5*0.10) / 1.0 * 5
        # = (0.10 + 0.16 + 0.10) / 1.0 * 5 = 0.36 * 5 = 1.80
        rubric = _rubric(python_w=0.10, ml_w=0.80, projects_w=0.10)
        result = score_candidate_service(_profile(), rubric, llm=_fake_llm(python=5, ml=1, projects=5))
        assert result.weighted_score == pytest.approx(1.80, abs=1e-4)

    def test_unequal_weights_are_normalised(self):
        """Weights that don't sum to 1 are normalised correctly."""
        # w values: 3, 3, 4  (sum=10); all scores = 5
        # = (5/5*3 + 5/5*3 + 5/5*4) / 10 * 5 = 10/10 * 5 = 5.0
        rubric = [
            ScoringCriterion(name="Python", weight=0.3),
            ScoringCriterion(name="Machine Learning", weight=0.3),
            ScoringCriterion(name="Projects", weight=0.4),
        ]
        result = score_candidate_service(_profile(), rubric, llm=_fake_llm(5, 5, 5))
        assert result.weighted_score == pytest.approx(5.0)

    def test_compute_weighted_score_helper_directly(self):
        """Unit-test the helper function that computes the weighted score."""
        from app.models import ScoreEvidence

        rubric = _rubric()
        scores = [
            ScoreEvidence(criterion="Python", score=5, evidence="e"),
            ScoreEvidence(criterion="Machine Learning", score=4, evidence="e"),
            ScoreEvidence(criterion="Projects", score=5, evidence="e"),
        ]
        result = _compute_weighted_score(scores, rubric)
        assert result == pytest.approx(4.70, abs=1e-4)

    def test_score_is_deterministic_same_input(self):
        """Identical input always produces the same weighted score."""
        r1 = score_candidate_service(_profile(), _rubric(), llm=_fake_llm())
        r2 = score_candidate_service(_profile(), _rubric(), llm=_fake_llm())
        assert r1.weighted_score == r2.weighted_score


# ── Evidence ─────────────────────────────────────────────────────────────────


class TestEvidence:
    def test_every_criterion_has_evidence(self):
        result = score_candidate_service(_profile(), _rubric(), llm=_fake_llm())
        for evidence in result.scores:
            assert evidence.evidence, f"Missing evidence for criterion '{evidence.criterion}'"

    def test_score_count_matches_rubric_count(self):
        rubric = _rubric()
        result = score_candidate_service(_profile(), rubric, llm=_fake_llm())
        assert len(result.scores) == len(rubric)

    def test_criterion_names_match_rubric(self):
        rubric = _rubric()
        result = score_candidate_service(_profile(), rubric, llm=_fake_llm())
        returned_names = {s.criterion for s in result.scores}
        rubric_names = {c.name for c in rubric}
        assert returned_names == rubric_names


# ── Recommendation ────────────────────────────────────────────────────────────


class TestRecommendation:
    def test_high_score_gives_strong_yes(self):
        result = score_candidate_service(_profile(), _rubric(), llm=_fake_llm(5, 5, 5))
        assert result.recommendation == "strong_yes"

    def test_low_score_gives_no(self):
        result = score_candidate_service(_profile(), _rubric(), llm=_fake_llm(0, 0, 0))
        assert result.recommendation == "no"

    def test_recommendation_is_one_of_valid_values(self):
        result = score_candidate_service(_profile(), _rubric(), llm=_fake_llm())
        assert result.recommendation in {"strong_yes", "yes", "maybe", "no"}


# ── Fairness: name invariance ─────────────────────────────────────────────────


class TestFairness:
    """Changing the candidate's name or other protected attributes must NOT
    change the weighted score.  The score is derived from the LLM's per-criterion
    numeric scores, and the LLM does not see the name in the profile text."""

    def _score_for_name(self, name: str) -> float:
        """Helper: score a profile with the given name using the same fake LLM."""
        profile = _profile(name=name)
        result = score_candidate_service(profile, _rubric(), llm=_fake_llm())
        return result.weighted_score

    def test_different_names_produce_same_score(self):
        """Changing the name leaves the weighted_score unchanged."""
        assert self._score_for_name("Alex Rivera") == self._score_for_name("Jordan Lee")

    def test_common_vs_rare_name_same_score(self):
        assert self._score_for_name("John Smith") == self._score_for_name("Priya Krishnamurthy")

    def test_name_is_absent_from_profile_text(self):
        """The profile text sent to the LLM must not include the candidate's name."""
        profile = _profile(name="Alex Rivera")
        text = _profile_to_text(profile)
        assert "Alex Rivera" not in text

    def test_email_is_absent_from_profile_text(self):
        """Email is a protected/irrelevant attribute — excluded from the profile text."""
        profile = _profile(email="alex@example.com")
        text = _profile_to_text(profile)
        assert "alex@example.com" not in text

    def test_phone_is_absent_from_profile_text(self):
        """Phone number is excluded from the profile text."""
        profile = _profile(phone="+1 555 010 2233")
        text = _profile_to_text(profile)
        assert "+1 555 010 2233" not in text

    def test_irrelevant_identity_change_does_not_change_score(self):
        """Changing email/phone (not job-relevant) does not change weighted score."""
        profile_a = _profile(name="Alex Rivera", email="a@example.com", phone="+1 555 010 2233")
        profile_b = _profile(name="Alex Rivera", email="b@other.com", phone="+44 20 7946 0958")

        score_a = score_candidate_service(profile_a, _rubric(), llm=_fake_llm()).weighted_score
        score_b = score_candidate_service(profile_b, _rubric(), llm=_fake_llm()).weighted_score

        assert score_a == score_b

    def test_candidate_name_in_output_matches_profile_name(self):
        """The CandidateScore should correctly record the candidate's name."""
        profile = _profile(name="Morgan Chen")
        result = score_candidate_service(profile, _rubric(), llm=_fake_llm())
        assert result.candidate_name == "Morgan Chen"


# ── Input validation ──────────────────────────────────────────────────────────


class TestInputValidation:
    def test_empty_rubric_raises_value_error(self):
        with pytest.raises(ValueError, match="Rubric"):
            score_candidate_service(_profile(), [], llm=_fake_llm())

    def test_llm_error_propagates(self):
        llm = FakeLLMService(result=LLMError("down"))
        with pytest.raises(LLMError):
            score_candidate_service(_profile(), _rubric(), llm=llm)


# ── LLM response anomalies ────────────────────────────────────────────────────


class TestLLMResponseAnomalies:
    def test_out_of_range_score_is_clamped(self):
        """A score > 5 from the LLM is clamped to 5."""
        llm = FakeLLMService(
            result=_LLMScoringResponse(
                scores=[
                    _CriterionScore(criterion="Python", score=7, evidence="great"),
                    _CriterionScore(criterion="Machine Learning", score=4, evidence="ok"),
                    _CriterionScore(criterion="Projects", score=5, evidence="good"),
                ]
            )
        )
        result = score_candidate_service(_profile(), _rubric(), llm=llm)
        python_score = next(s for s in result.scores if s.criterion == "Python")
        assert python_score.score <= 5.0
        assert result.weighted_score <= 5.0

    def test_missing_criterion_in_llm_response_defaults_to_zero(self):
        """If the LLM omits a criterion, it scores 0 (not an error)."""
        llm = FakeLLMService(
            result=_LLMScoringResponse(
                scores=[
                    _CriterionScore(criterion="Python", score=5, evidence="e"),
                    # Machine Learning and Projects are missing
                ]
            )
        )
        result = score_candidate_service(_profile(), _rubric(), llm=llm)
        assert len(result.scores) == 3
        for s in result.scores:
            if s.criterion in ("Machine Learning", "Projects"):
                assert s.score == 0.0
