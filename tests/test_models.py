import pytest
from pydantic import ValidationError

from app.models import (
    CandidateProfile,
    CandidateScore,
    JobDescription,
    ScoreEvidence,
    ScoringCriterion,
)


class TestCandidateProfile:
    def test_minimal_profile_gets_safe_defaults(self):
        p = CandidateProfile(name="Alex Rivera")
        assert p.skills == [] and p.projects == [] and p.experience == []
        assert p.email is None and p.years_of_experience == 0

    def test_name_is_required(self):
        with pytest.raises(ValidationError):
            CandidateProfile()
        with pytest.raises(ValidationError):
            CandidateProfile(name="")

    def test_negative_experience_rejected(self):
        with pytest.raises(ValidationError):
            CandidateProfile(name="A", years_of_experience=-1)

    @pytest.mark.parametrize("bad", ["N/A", "", "not an email", "a@b"])
    def test_invalid_email_becomes_none(self, bad):
        assert CandidateProfile(name="A", email=bad).email is None

    def test_valid_email_kept(self):
        assert CandidateProfile(name="A", email=" a@example.com ").email == "a@example.com"

    @pytest.mark.parametrize("good", ["+1 555 010 2233", "(555) 010-2233", "5550102233"])
    def test_valid_phone_kept(self, good):
        assert CandidateProfile(name="A", phone=good).phone == good

    @pytest.mark.parametrize("bad", ["N/A", "", "call me", "123"])
    def test_invalid_phone_becomes_none(self, bad):
        assert CandidateProfile(name="A", phone=bad).phone is None


class TestJobDescription:
    def test_minimal_job(self):
        j = JobDescription(title="Engineer")
        assert j.required_skills == [] and j.minimum_experience == 0

    def test_title_required(self):
        with pytest.raises(ValidationError):
            JobDescription(title="")

    def test_negative_min_experience_rejected(self):
        with pytest.raises(ValidationError):
            JobDescription(title="Engineer", minimum_experience=-2)


class TestScoring:
    def test_score_must_be_0_to_5(self):
        with pytest.raises(ValidationError):
            ScoreEvidence(criterion="Python", score=6, evidence="x")
        with pytest.raises(ValidationError):
            ScoreEvidence(criterion="Python", score=-1, evidence="x")

    def test_fractional_score_allowed(self):
        assert ScoreEvidence(criterion="Python", score=3.5, evidence="x").score == 3.5

    def test_score_requires_evidence(self):
        with pytest.raises(ValidationError):
            ScoreEvidence(criterion="Python", score=4, evidence="")

    def test_weight_must_be_positive_and_at_most_1(self):
        with pytest.raises(ValidationError):
            ScoringCriterion(name="Python", weight=0)
        with pytest.raises(ValidationError):
            ScoringCriterion(name="Python", weight=1.5)

    def test_candidate_score_valid(self):
        s = CandidateScore(
            candidate_name="Alex Rivera",
            scores=[ScoreEvidence(criterion="Python", score=4, evidence="5 years of Python")],
            weighted_score=4.0,
            recommendation="yes",
        )
        assert s.scores[0].score == 4

    def test_unknown_recommendation_rejected(self):
        with pytest.raises(ValidationError):
            CandidateScore(candidate_name="A", weighted_score=3, recommendation="hire now!!")
