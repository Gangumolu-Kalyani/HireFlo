"""Rubric and scoring models."""

from typing import Literal

from pydantic import BaseModel, Field

Recommendation = Literal["strong_yes", "yes", "maybe", "no"]


class ScoringCriterion(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    weight: float = Field(gt=0, le=1, description="Relative weight (0-1]")


class ScoreEvidence(BaseModel):
    criterion: str = Field(min_length=1)
    score: int = Field(ge=0, le=5)
    # Every score must be backed by resume evidence - empty evidence is rejected.
    evidence: str = Field(min_length=1, description="Quote or fact taken from the resume")
    reasoning: str = ""


class CandidateScore(BaseModel):
    candidate_name: str = Field(min_length=1)
    scores: list[ScoreEvidence] = Field(default_factory=list)
    weighted_score: float = Field(ge=0, le=5)
    recommendation: Recommendation
