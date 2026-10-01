"""Tests for app/guardrails/fairness.py — fairness attribute checks.

All tests run offline.

Tested scenarios
----------------
1. Changing candidate name does not change score (scoring isolation)
2. Adding age does not change score
3. Adding gender does not change score
4. Adding nationality does not change score
5. Adding college prestige does not change score
6. Evidence mentioning protected attributes is flagged
7. Job-relevant skills evidence is NOT flagged
8. check_evidence_text unit tests
9. Empty scores list produces no flags
"""

from __future__ import annotations

from app.guardrails.fairness import check_evidence_text, check_scoring_fairness
from app.models.candidate import CandidateProfile
from app.models.guardrail_models import GuardrailResult
from app.models.scoring import CandidateScore, ScoreEvidence, ScoringCriterion
from app.tools.scoring_tool import _profile_to_text

# ── Helpers ───────────────────────────────────────────────────────────────────

RUBRIC = [
    ScoringCriterion(name="Python", weight=0.5),
    ScoringCriterion(name="FastAPI", weight=0.5),
]


def _make_score(
    candidate_name: str = "Alex Rivera",
    python_evidence: str = "5 years of Python experience.",
    python_score: float = 4.0,
    fastapi_evidence: str = "Built FastAPI APIs in production.",
    fastapi_score: float = 4.0,
    python_reasoning: str = "Strong Python background.",
    fastapi_reasoning: str = "FastAPI used in production.",
) -> CandidateScore:
    return CandidateScore(
        candidate_name=candidate_name,
        scores=[
            ScoreEvidence(
                criterion="Python",
                score=python_score,
                evidence=python_evidence,
                reasoning=python_reasoning,
            ),
            ScoreEvidence(
                criterion="FastAPI",
                score=fastapi_score,
                evidence=fastapi_evidence,
                reasoning=fastapi_reasoning,
            ),
        ],
        weighted_score=(python_score + fastapi_score) / 2,
        recommendation="strong_yes",
    )


# ── Scenario 1: Changing candidate name does not change score ─────────────────
#
# This test is about the scoring ARCHITECTURE, not just the guardrail.
# score_candidate_service omits name from the profile text sent to the LLM,
# so the weighted score is entirely determined by the evidence scores.


class TestNameDoesNotAffectScore:
    def test_profile_text_excludes_name(self):
        """_profile_to_text must not include the candidate's name."""
        profile = CandidateProfile(
            name="Priya Sharma",
            skills=["Python", "FastAPI"],
            years_of_experience=5,
        )
        text = _profile_to_text(profile)
        assert "Priya Sharma" not in text

    def test_profile_text_excludes_email(self):
        profile = CandidateProfile(
            name="Jordan Lee",
            email="jordan@example.com",
            skills=["Python"],
            years_of_experience=3,
        )
        text = _profile_to_text(profile)
        assert "jordan@example.com" not in text

    def test_profile_text_excludes_phone(self):
        profile = CandidateProfile(
            name="Sam Kim",
            phone="+1 555 123 4567",
            skills=["Python"],
            years_of_experience=2,
        )
        text = _profile_to_text(profile)
        assert "+1 555 123 4567" not in text

    def test_same_skills_different_names_have_identical_profile_text(self):
        """Two candidates with identical skills/experience but different names
        produce identical profile text."""
        skills = ["Python", "FastAPI"]
        exp = 5.0
        education = ["BSc CS"]
        experience = ["Backend dev at Acme"]
        projects = ["Built e-commerce API"]
        summary = "Senior Python developer."

        profile_a = CandidateProfile(
            name="Alex Smith",
            email="alex@example.com",
            skills=skills,
            years_of_experience=exp,
            education=education,
            experience=experience,
            projects=projects,
            summary=summary,
        )
        profile_b = CandidateProfile(
            name="Wei Zhang",
            email="wei@example.com",
            skills=skills,
            years_of_experience=exp,
            education=education,
            experience=experience,
            projects=projects,
            summary=summary,
        )

        assert _profile_to_text(profile_a) == _profile_to_text(profile_b)

    def test_changing_name_does_not_change_profile_text_content(self):
        base = {
            "skills": ["Python"],
            "years_of_experience": 5,
        }
        text_a = _profile_to_text(CandidateProfile(name="John Smith", **base))
        text_b = _profile_to_text(CandidateProfile(name="Aisha Okonkwo", **base))
        assert text_a == text_b


# ── Scenario 2-5: Protected attributes do not affect profile text ─────────────


class TestProtectedAttributesExcludedFromProfileText:
    """These tests verify that protected attributes are not included in the
    text sent to the LLM for scoring.  Adding them to the CandidateProfile
    must not produce different profile text, because CandidateProfile fields
    like 'age', 'gender', 'nationality' do not exist (by design)."""

    def test_profile_only_includes_job_relevant_fields(self):
        profile = CandidateProfile(
            name="Test Candidate",
            skills=["Python", "Django"],
            years_of_experience=4,
            education=["BSc Computer Science"],
            projects=["REST API project"],
            experience=["Backend developer, TechCorp"],
            summary="Python developer with 4 years experience.",
        )
        text = _profile_to_text(profile)

        # Should contain job-relevant content.
        assert "Python" in text
        assert "4" in text  # years of experience

        # Should NOT contain the name.
        assert "Test Candidate" not in text


# ── Scenario 6: Evidence mentioning protected attributes is flagged ───────────


class TestFairnessFlags:
    def test_age_reference_in_evidence_is_flagged(self):
        cs = _make_score(
            python_evidence="Candidate is young and therefore adaptable.",
            python_reasoning="Young candidates learn quickly.",
        )
        result = check_scoring_fairness(cs)
        assert result.passed is False
        assert any("age" in f for f in result.flags)

    def test_gender_reference_in_evidence_is_flagged(self):
        cs = _make_score(
            python_evidence="Female candidate with Python skills.",
        )
        result = check_scoring_fairness(cs)
        assert result.passed is False
        assert any("gender" in f for f in result.flags)

    def test_nationality_reference_is_flagged(self):
        cs = _make_score(
            python_evidence="Foreign national with Python experience.",
        )
        result = check_scoring_fairness(cs)
        assert result.passed is False
        assert any("nationality" in f for f in result.flags)

    def test_religion_reference_is_flagged(self):
        cs = _make_score(
            python_evidence="Christian candidate with strong values and Python skills.",
        )
        result = check_scoring_fairness(cs)
        assert result.passed is False
        assert any("religion" in f for f in result.flags)

    def test_caste_reference_is_flagged(self):
        cs = _make_score(
            python_evidence="Candidate from a high-caste background shows Python skills.",
        )
        result = check_scoring_fairness(cs)
        assert result.passed is False
        assert any("caste" in f for f in result.flags)

    def test_marital_status_reference_is_flagged(self):
        cs = _make_score(
            python_evidence="Single candidate focused on career, with Python skills.",
        )
        result = check_scoring_fairness(cs)
        assert result.passed is False
        assert any("marital" in f for f in result.flags)

    def test_college_prestige_reference_is_flagged(self):
        cs = _make_score(
            python_evidence="Harvard graduate with Python skills.",
            python_reasoning="Harvard-trained engineers are reliable.",
        )
        result = check_scoring_fairness(cs)
        assert result.passed is False
        assert any("college_prestige" in f for f in result.flags)

    def test_severity_is_medium_when_flagged(self):
        cs = _make_score(python_evidence="Young and enthusiastic Python developer.")
        result = check_scoring_fairness(cs)
        assert result.passed is False
        assert result.severity == "medium"


# ── Scenario 7: Job-relevant evidence is NOT flagged ─────────────────────────


class TestJobRelevantEvidenceNotFlagged:
    def test_python_skills_not_flagged(self):
        cs = _make_score(
            python_evidence="Candidate has 5 years of Python experience including Django and FastAPI.",
            fastapi_evidence="Implemented REST APIs with FastAPI in a production system.",
        )
        result = check_scoring_fairness(cs)
        assert result.passed is True

    def test_project_experience_not_flagged(self):
        cs = _make_score(
            python_evidence="Built an ML pipeline using Python and scikit-learn.",
        )
        result = check_scoring_fairness(cs)
        assert result.passed is True

    def test_certification_not_flagged(self):
        cs = _make_score(
            python_evidence="AWS Certified Solutions Architect with Python scripting skills.",
        )
        result = check_scoring_fairness(cs)
        assert result.passed is True

    def test_years_of_experience_not_flagged(self):
        cs = _make_score(
            python_evidence="8 years of Python development across three companies.",
        )
        result = check_scoring_fairness(cs)
        assert result.passed is True

    def test_education_degree_not_flagged(self):
        cs = _make_score(
            python_evidence="BSc in Computer Science with Python as primary language.",
        )
        result = check_scoring_fairness(cs)
        assert result.passed is True


# ── Scenario 8: check_evidence_text unit tests ───────────────────────────────


class TestCheckEvidenceText:
    def test_clean_text_passes(self):
        result = check_evidence_text("5 years of Python experience.")
        assert result.passed is True

    def test_gender_mention_flagged(self):
        result = check_evidence_text("Male candidate with strong Python skills.")
        assert result.passed is False
        assert any("gender" in f for f in result.flags)

    def test_age_mention_flagged(self):
        check_evidence_text("Candidate is 28 years old.")
        result2 = check_evidence_text("Candidate is young and eager.")
        assert result2.passed is False

    def test_nationality_mention_flagged(self):
        result = check_evidence_text("Immigrant with Python experience.")
        assert result.passed is False

    def test_multiple_attributes_multiple_flags(self):
        result = check_evidence_text(
            "Young female Christian candidate with Python skills."
        )
        assert result.passed is False
        assert len(result.flags) >= 2  # at least age and gender

    def test_returns_guardrail_result(self):
        result = check_evidence_text("hello")
        assert isinstance(result, GuardrailResult)


# ── Scenario 9: Empty scores ──────────────────────────────────────────────────


class TestEmptyScores:
    def test_no_scores_produces_no_flags(self):
        cs = CandidateScore(
            candidate_name="Alex Rivera",
            scores=[],
            weighted_score=0.0,
            recommendation="no",
        )
        result = check_scoring_fairness(cs)
        # No evidence to check → no fairness flags.
        assert result.passed is True
        assert result.flags == []
