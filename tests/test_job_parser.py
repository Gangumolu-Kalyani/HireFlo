import pytest

from app.models import JobDescription
from app.services.job_parser import parse_job_description
from app.services.llm_service import LLMError
from tests.conftest import FakeLLMService

JOB = JobDescription(
    title="Senior Python Backend Engineer",
    required_skills=["Python", "FastAPI", "SQL", "Docker"],
    preferred_skills=["LangChain", "GitHub Actions", "AWS"],
    minimum_experience=4,
)


def test_parses_job_with_mocked_llm(sample_job):
    llm = FakeLLMService(result=JOB)
    job = parse_job_description(sample_job, llm=llm)

    assert job == JOB
    call = llm.calls[0]
    assert call["schema"] is JobDescription
    user = call["user"]
    open_tag = user.index("<job_description>")
    assert open_tag < user.index("Senior Python") < user.index("</job_description>")


def test_empty_job_rejected_before_calling_llm():
    llm = FakeLLMService(result=JOB)
    with pytest.raises(ValueError):
        parse_job_description("  ", llm=llm)
    assert llm.calls == []


def test_llm_failure_propagates(sample_job):
    with pytest.raises(LLMError):
        parse_job_description(sample_job, llm=FakeLLMService(result=LLMError("down")))
