"""Recruitment agent package.

Phase 4 — LangGraph-based orchestration of the Phase 3 recruitment tools.

Public surface:

    from app.agent import build_graph
    from app.agent.runner import run_recruitment, approve_interview, reject_interview
    from app.agent.state import RecruitmentState, TrajectoryEntry
"""

from app.agent.graph import build_graph

__all__ = ["build_graph"]
