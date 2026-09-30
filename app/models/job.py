"""Structured job description."""

from pydantic import BaseModel, Field


class JobDescription(BaseModel):
    title: str = Field(min_length=1, description="Job title")
    description: str = Field(default="", description="Short summary of the role")
    required_skills: list[str] = Field(default_factory=list, description="Must-have skills")
    preferred_skills: list[str] = Field(default_factory=list, description="Nice-to-have skills")
    minimum_experience: float | None = Field(
        default=None, ge=0, description="Minimum years of experience, if stated"
    )
    education: str | None = Field(default=None, description="Education requirement, if stated")
    responsibilities: list[str] = Field(default_factory=list, description="Key responsibilities")
