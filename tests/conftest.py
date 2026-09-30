"""Shared test fixtures. No test here talks to a real LLM."""

from pathlib import Path

import pytest
from pydantic import BaseModel

DATA_DIR = Path(__file__).parent / "data"


class FakeLLMService:
    """Stands in for LLMService: records prompts and returns a canned result."""

    def __init__(self, result: BaseModel | Exception | None = None):
        self.result = result
        self.calls: list[dict] = []

    def structured_call(self, system_prompt: str, user_content: str, schema: type[BaseModel]):
        self.calls.append({"system": system_prompt, "user": user_content, "schema": schema})
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def sample_resume() -> str:
    return (DATA_DIR / "sample_resume.txt").read_text(encoding="utf-8")


@pytest.fixture
def sample_job() -> str:
    return (DATA_DIR / "sample_job.txt").read_text(encoding="utf-8")
