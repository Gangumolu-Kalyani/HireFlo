"""parse_resume tool — thin @tool wrapper around the resume_parser service.

Architecture:
    LangGraph
        ↓
    parse_resume  (this file)
        ↓
    resume_parser service
        ↓
    LLMService
        ↓
    CandidateProfile

The tool is deliberately thin: all parsing, security wrapping, and size-limit
logic lives in ``app.services.resume_parser``.  The tool only bridges the
LangGraph tool-calling interface to that service.
"""

from langchain_core.tools import tool

from app.models import CandidateProfile
from app.services.llm_service import LLMService
from app.services.resume_parser import parse_resume as _parse_resume


def parse_resume_service(resume_text: str, llm: LLMService | None = None) -> CandidateProfile:
    """Call the resume parser service directly (bypassing the @tool wrapper).

    Tests and other services call this function so they can inject a fake LLM
    without needing to invoke the LangChain tool machinery.

    Raises:
        ValueError: if ``resume_text`` is empty or exceeds the size limit.
        LLMError:   if the underlying LLM call fails.
    """
    return _parse_resume(resume_text, llm=llm)


@tool
def parse_resume(resume_text: str) -> dict:
    """Parse a resume and return a structured candidate profile.

    Treats ``resume_text`` as UNTRUSTED DATA: the text is wrapped in delimiters
    inside the prompt and the system prompt instructs the model never to follow
    instructions embedded in the resume.

    Args:
        resume_text: Raw resume text from a candidate.  Must not be empty and
            must not exceed 50,000 characters.

    Returns:
        A dict representation of ``CandidateProfile`` with fields: name, email,
        phone, skills, years_of_experience, education, projects, experience,
        summary.

    Raises:
        ValueError: if the input is empty or too large.
        LLMError:   if the LLM call fails.
    """
    profile = _parse_resume(resume_text)
    return profile.model_dump()
