"""Structured candidate profile extracted from a resume."""

import re

from pydantic import BaseModel, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CandidateProfile(BaseModel):
    name: str = Field(min_length=1, description="Candidate name as written on the resume")
    email: str | None = Field(default=None, description="Email address, if present")
    skills: list[str] = Field(default_factory=list, description="Technical and professional skills")
    years_of_experience: float = Field(default=0, ge=0, description="Total years of work experience")
    education: list[str] = Field(default_factory=list, description="Degrees / certifications")
    projects: list[str] = Field(default_factory=list, description="Notable projects, one line each")
    experience: list[str] = Field(
        default_factory=list, description="Work history, one line per role"
    )
    summary: str = Field(default="", description="Neutral 1-3 sentence summary of the resume")

    @field_validator("email")
    @classmethod
    def _drop_invalid_email(cls, value: str | None) -> str | None:
        # LLMs sometimes return "N/A" or "" - store None rather than a bogus address.
        if value is None:
            return None
        value = value.strip()
        return value if _EMAIL_RE.match(value) else None
