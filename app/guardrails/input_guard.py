"""Input validation guardrail.

This module validates raw text before it is sent to the LLM.

Checks performed
----------------
1. Empty / whitespace-only input          → severity HIGH
2. Input exceeds maximum length           → severity HIGH
3. Input is suspiciously short            → severity LOW (warn only)
4. Injection-like content detected        → delegated to injection.detect_injection()

Design principles
-----------------
- The original text is never silently modified.
- Validation is cheap and runs before any LLM call.
- Structured GuardrailResult is returned; the caller decides what to do.
- Logging must never include secrets.
"""

from __future__ import annotations

from app.guardrails.injection import detect_injection
from app.models.guardrail_models import GuardrailResult

# ── Limits ────────────────────────────────────────────────────────────────────

MAX_RESUME_CHARS: int = 50_000       # same limit used in resume_parser service
MAX_JD_CHARS: int = 50_000
MIN_MEANINGFUL_CHARS: int = 20       # shorter texts are flagged as suspiciously thin


def validate_input(
    text: str,
    *,
    label: str = "input",
    max_chars: int = MAX_RESUME_CHARS,
    run_injection_check: bool = True,
) -> GuardrailResult:
    """Validate a raw text input before passing it to the LLM.

    Args:
        text:                 The raw text to validate (resume, JD, etc.).
        label:                Human-readable label used in reason messages (e.g. ``"resume"``).
        max_chars:            Maximum allowed character count.
        run_injection_check:  If True (default), also run the injection detector.

    Returns:
        A :class:`~app.models.guardrail_models.GuardrailResult`.

    Notes:
        - The original text is never modified.
        - Injection detection is delegated to :func:`~app.guardrails.injection.detect_injection`.
    """
    flags: list[str] = []
    reasons: list[str] = []
    severities: list[str] = []

    # ── Check 1: empty / whitespace ───────────────────────────────────────────
    if not text or not text.strip():
        return GuardrailResult(
            passed=False,
            severity="high",
            flags=["empty_input"],
            reasons=[f"The {label} text is empty or contains only whitespace."],
        )

    # ── Check 2: exceeds max length ───────────────────────────────────────────
    if len(text) > max_chars:
        flags.append("input_too_long")
        reasons.append(
            f"The {label} text is {len(text):,} characters, which exceeds the "
            f"{max_chars:,}-character limit."
        )
        severities.append("high")

    # ── Check 3: suspiciously short ───────────────────────────────────────────
    if 0 < len(text.strip()) < MIN_MEANINGFUL_CHARS:
        flags.append("input_too_short")
        reasons.append(
            f"The {label} text is only {len(text.strip())} characters — suspiciously short "
            "for a meaningful document."
        )
        severities.append("low")

    # ── Check 4: injection detection ─────────────────────────────────────────
    if run_injection_check:
        injection_result = detect_injection(text)
        if not injection_result.passed:
            flags.extend(injection_result.flags)
            reasons.extend(injection_result.reasons)
            if injection_result.severity:
                severities.append(injection_result.severity)

    # ── Aggregate result ──────────────────────────────────────────────────────
    if not flags:
        return GuardrailResult(
            passed=True,
            severity=None,
            flags=[],
            reasons=[],
        )

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
