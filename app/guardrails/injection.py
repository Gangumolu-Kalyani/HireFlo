"""Prompt-injection detector for resume and job-description text.

This module provides lightweight, rule-based detection of text patterns that
suggest a caller is attempting to hijack the agent's instructions.

IMPORTANT LIMITATIONS
---------------------
Regex pattern matching is a *mitigation*, not a guarantee.  A sophisticated
adversary who avoids the listed phrases can still attempt injection.  This
layer is one of several defences:

    1. This detector (flag early)
    2. Delimiter-wrapping in the prompt (contain the input)
    3. System-prompt priority in the LLM (instruct the model)
    4. Output validation (catch manipulation that slipped through)

Design principles
-----------------
- Do NOT auto-reject every resume that contains unusual text.
- Flag suspicious content and attach metadata so the graph can decide whether
  to block, warn, or continue.
- Return structured GuardrailResult objects (never raw strings).
- Only operational/audit information is stored — no LLM internals.
"""

from __future__ import annotations

import re

from app.models.guardrail_models import GuardrailResult

# ── Pattern catalogue ──────────────────────────────────────────────────────────
#
# Patterns are matched case-insensitively against the full input text.
# Each entry is (compiled_pattern, human_readable_reason, severity).
#
# Severity:
#   "high"   — strong injection signal (direct instruction override)
#   "medium" — suspicious but ambiguous
#   "low"    — mild hint; might be benign in some contexts

_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    # Direct override instructions
    (
        re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
        "Attempt to override previous instructions detected.",
        "high",
    ),
    (
        re.compile(r"ignore\s+(all\s+)?your\s+instructions", re.IGNORECASE),
        "Attempt to override agent instructions detected.",
        "high",
    ),
    (
        re.compile(r"override\s+your\s+instructions", re.IGNORECASE),
        "Attempt to override agent instructions detected.",
        "high",
    ),
    (
        re.compile(r"follow\s+these\s+instructions\s+instead", re.IGNORECASE),
        "Attempt to replace agent instructions detected.",
        "high",
    ),
    (
        re.compile(r"disregard\s+(all\s+)?(previous|prior|your)\s+instructions", re.IGNORECASE),
        "Attempt to disregard instructions detected.",
        "high",
    ),
    # System / developer role hijacking
    (
        re.compile(r"\bsystem\s+message\b", re.IGNORECASE),
        "Attempt to inject a system message detected.",
        "high",
    ),
    (
        re.compile(r"\bdeveloper\s+message\b", re.IGNORECASE),
        "Attempt to inject a developer message detected.",
        "high",
    ),
    (
        re.compile(r"\bsystem\s+prompt\b", re.IGNORECASE),
        "Reference to system prompt found in input — possible prompt-injection attempt.",
        "high",
    ),
    (
        re.compile(r"you\s+are\s+now\s+(in\s+)?\w", re.IGNORECASE),
        "Attempt to redefine agent role or mode detected.",
        "high",
    ),
    (
        re.compile(r"act\s+as\s+(if\s+you\s+(are|were)\s+)?(a\s+)?new\b", re.IGNORECASE),
        "Attempt to impersonate a different agent detected.",
        "medium",
    ),
    # Secret / credential disclosure
    (
        re.compile(r"reveal\s+(your\s+)?(system\s+)?(prompt|instructions|api\s*key|secret|password|token)", re.IGNORECASE),
        "Request to reveal secrets or instructions detected.",
        "high",
    ),
    (
        re.compile(r"print\s+(your\s+)?(api\s*key|secret|password|token|instructions)", re.IGNORECASE),
        "Request to print secrets or instructions detected.",
        "high",
    ),
    (
        re.compile(r"show\s+(me\s+)?(your\s+)?(api\s*key|secret|password|token|prompt|instructions)", re.IGNORECASE),
        "Request to disclose internal instructions or credentials detected.",
        "high",
    ),
    (
        re.compile(r"what\s+is\s+your\s+(api\s*key|secret|password|token|system\s+prompt)", re.IGNORECASE),
        "Request to expose internal state detected.",
        "high",
    ),
    # Ranking / score manipulation
    (
        re.compile(r"rank\s+(me|this\s+candidate)\s+(as\s+)?(the\s+)?(best|first|top|highest)", re.IGNORECASE),
        "Attempt to manipulate candidate ranking detected.",
        "high",
    ),
    (
        re.compile(r"set\s+(every|all)\s+(score|scores)\s+to\s+\d", re.IGNORECASE),
        "Attempt to force scores detected.",
        "high",
    ),
    (
        re.compile(r"give\s+(me|this\s+candidate)\s+(a\s+)?(score\s+of\s+)?5", re.IGNORECASE),
        "Attempt to force a maximum score detected.",
        "high",
    ),
    (
        re.compile(r"recommend\s+(strong_yes|yes)\s+for\s+(me|this\s+candidate)", re.IGNORECASE),
        "Attempt to force a recommendation detected.",
        "high",
    ),
    (
        re.compile(r"strong_yes\b", re.IGNORECASE),
        "Recommendation label injected into input text.",
        "medium",
    ),
    # Admin / jailbreak patterns
    (
        re.compile(r"\badmin\s+mode\b", re.IGNORECASE),
        "Attempt to activate a privileged mode detected.",
        "high",
    ),
    (
        re.compile(r"\bjailbreak\b", re.IGNORECASE),
        "Jailbreak attempt detected.",
        "high",
    ),
    (
        re.compile(r"\bdan\s+mode\b", re.IGNORECASE),
        "DAN-style jailbreak attempt detected.",
        "high",
    ),
    # Prompt delimiter injection (attempt to break out of <resume> block)
    (
        re.compile(r"</?(resume|candidate_profile|job_description)\s*>", re.IGNORECASE),
        "Attempt to inject or escape XML/HTML delimiters detected.",
        "medium",
    ),
    # Generic instruction patterns
    (
        re.compile(r"\bnew\s+instructions?\b", re.IGNORECASE),
        "Attempt to introduce new instructions detected.",
        "medium",
    ),
    (
        re.compile(r"\byour\s+(rules?|constraints?)\s+(are|have been)\s+(changed|updated|removed|disabled)", re.IGNORECASE),
        "Attempt to modify agent rules detected.",
        "high",
    ),
]


def detect_injection(text: str) -> GuardrailResult:
    """Scan ``text`` for prompt-injection patterns.

    Args:
        text: The resume or job-description text to check.

    Returns:
        A :class:`~app.models.guardrail_models.GuardrailResult` with:
        - ``passed``: True if no suspicious patterns were found.
        - ``severity``: Highest severity found (``"high"`` | ``"medium"`` | ``"low"`` | None).
        - ``flags``: List of short flag identifiers.
        - ``reasons``: Human-readable descriptions of each match.

    Notes:
        - This detector does not modify ``text``.
        - A ``passed=False`` result does not automatically block the workflow;
          the calling graph node applies the severity policy.
        - Regex detection is a mitigation, not a guarantee.
    """
    if not text or not text.strip():
        return GuardrailResult(
            passed=True,
            severity=None,
            flags=[],
            reasons=[],
        )

    flags: list[str] = []
    reasons: list[str] = []
    severities: list[str] = []

    for pattern, reason, severity in _PATTERNS:
        if pattern.search(text):
            flag = _pattern_to_flag(pattern)
            if flag not in flags:  # de-duplicate
                flags.append(flag)
                reasons.append(reason)
                severities.append(severity)

    if not flags:
        return GuardrailResult(
            passed=True,
            severity=None,
            flags=[],
            reasons=[],
        )

    # Escalate to the highest severity found.
    top_severity = _top_severity(severities)

    return GuardrailResult(
        passed=False,
        severity=top_severity,
        flags=flags,
        reasons=reasons,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}


def _top_severity(severities: list[str]) -> str:
    return max(severities, key=lambda s: _SEVERITY_RANK.get(s, 0))


def _pattern_to_flag(pattern: re.Pattern[str]) -> str:
    """Convert a compiled pattern to a short, ASCII-safe flag identifier."""
    raw = pattern.pattern[:40]
    # Replace non-alphanumeric runs with underscores.
    flag = re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").lower()
    return flag
