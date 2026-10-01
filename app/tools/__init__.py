"""Recruitment agent tools.

Phase 4 (LangGraph) imports all four tools from here:

    from app.tools import (
        parse_resume,
        score_candidate,
        check_availability,
        propose_interview,
    )

Each tool is also individually importable from its own module:

    from app.tools.resume_tool      import parse_resume, parse_resume_service
    from app.tools.scoring_tool     import score_candidate, score_candidate_service
    from app.tools.availability_tool import check_availability, check_availability_service
    from app.tools.interview_tool   import propose_interview, propose_interview_service
"""

from app.tools.availability_tool import check_availability
from app.tools.interview_tool import propose_interview
from app.tools.resume_tool import parse_resume
from app.tools.scoring_tool import score_candidate

__all__ = [
    "check_availability",
    "parse_resume",
    "propose_interview",
    "score_candidate",
]
