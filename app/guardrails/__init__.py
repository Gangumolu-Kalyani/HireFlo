"""Guardrails package for HireFlo — Phase 5.

Provides rule-based checks that sit between untrusted input and the agent,
and between LLM output and application logic.

Public surface
--------------

    from app.guardrails.injection import detect_injection
    from app.guardrails.input_guard import validate_input
    from app.guardrails.output_guard import validate_scoring_output
    from app.guardrails.fairness import check_scoring_fairness

Severity policy
---------------
LOW     — continue but record the flag in state["guardrail_flags"]
MEDIUM  — continue only after recording the flag; human review is required
HIGH    — stop immediately; set final_status = "GUARDRAIL_BLOCKED"

This policy is enforced in the LangGraph nodes (app/agent/graph.py).
The guardrail functions themselves only detect and classify — they do not
route or raise; that responsibility stays in the graph.
"""

from app.guardrails.fairness import check_scoring_fairness
from app.guardrails.injection import detect_injection
from app.guardrails.input_guard import validate_input
from app.guardrails.output_guard import validate_scoring_output

__all__ = [
    "check_scoring_fairness",
    "detect_injection",
    "validate_input",
    "validate_scoring_output",
]
