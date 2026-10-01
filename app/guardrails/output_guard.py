"""LLM output validation guardrail.

Validates scoring output produced by the LLM against the application's
authoritative rules:

    - Every criterion in the rubric must have a score.
    - Scores must be in [0, 5].
    - Evidence must not be empty.
    - Reasoning must not be empty.
    - No unexpected criteria are accepted.
    - Weights must be positive.
    - The Python-computed weighted score is always used (never the LLM's).

WHY THIS EXISTS
---------------
The LLM is told the rules in its system prompt, but it can occasionally:
  - Return out-of-range scores (e.g. 999, -1).
  - Invent criteria not in the rubric.
  - Produce empty evidence strings.
  - Return suspiciously convenient scores when prompted to manipulate.

This layer is the last line of defence before scoring results are accepted.

POLICY
------
Validation failures are classified by severity:

  LOW     — minor issue (e.g. slightly malformed reasoning) — accepted with flag
  MEDIUM  — notable issue (e.g. unknown criterion returned) — accepted with flag
  HIGH    — critical issue (e.g. wildly out-of-range score) — rejected

Scores are clamped by ``score_candidate_service`` BEFORE reaching here
(see ``scoring_tool._build_score_evidence``).  This guard catches anything
that survived clamping or was introduced after the service layer.
"""

from __future__ import annotations

from app.models.guardrail_models import GuardrailResult
from app.models.scoring import CandidateScore, ScoreEvidence, ScoringCriterion

# ── Thresholds ────────────────────────────────────────────────────────────────

SCORE_MIN: float = 0.0
SCORE_MAX: float = 5.0

# Evidence / reasoning that suspiciously references injection artefacts.
_SUSPICIOUS_EVIDENCE_PHRASES: list[str] = [
    "told me to give",
    "told me to score",
    "instructed me to",
    "admin mode",
    "ignore previous",
    "override",
    "jailbreak",
    "the candidate said to",
    "as instructed by the candidate",
    "candidate requested",
    "candidate told",
    "deserves to win",
    "deserves the highest",
    "give this candidate",
    "automatic",
]


def validate_scoring_output(
    candidate_score: CandidateScore,
    rubric: list[ScoringCriterion],
) -> GuardrailResult:
    """Validate a ``CandidateScore`` produced by the scoring service.

    Checks performed:
        1. Every rubric criterion has a corresponding ScoreEvidence entry.
        2. Each score is within [0, 5].
        3. Evidence is non-empty for every criterion.
        4. Reasoning is non-empty for every criterion.
        5. No unexpected criteria appear in the scores.
        6. Weighted score is within [0, 5].
        7. Evidence does not contain suspicious manipulation phrases.
        8. Rubric weights are valid (positive).

    Args:
        candidate_score: The ``CandidateScore`` to validate.
        rubric:          The authoritative rubric used for this run.

    Returns:
        A :class:`~app.models.guardrail_models.GuardrailResult`.
    """
    flags: list[str] = []
    reasons: list[str] = []
    severities: list[str] = []

    rubric_names: set[str] = {c.name for c in rubric}
    scored_names: set[str] = {s.criterion for s in candidate_score.scores}

    # ── Check 1: missing criteria ─────────────────────────────────────────────
    missing = rubric_names - scored_names
    for name in sorted(missing):
        flags.append("missing_criterion")
        reasons.append(f"Criterion '{name}' is in the rubric but missing from the scores.")
        severities.append("medium")

    # ── Check 2 & 3 & 4: per-score validation ────────────────────────────────
    for evidence in candidate_score.scores:
        _validate_evidence(evidence, flags, reasons, severities)

    # ── Check 5: unexpected criteria ─────────────────────────────────────────
    unexpected = scored_names - rubric_names
    for name in sorted(unexpected):
        flags.append("unexpected_criterion")
        reasons.append(
            f"Criterion '{name}' appears in scores but is not in the rubric — "
            "possible score injection."
        )
        severities.append("medium")

    # ── Check 6: weighted score range ────────────────────────────────────────
    ws = candidate_score.weighted_score
    if ws < SCORE_MIN or ws > SCORE_MAX:
        flags.append("weighted_score_out_of_range")
        reasons.append(
            f"Weighted score {ws} is outside the valid range [{SCORE_MIN}, {SCORE_MAX}]. "
            "The Python-computed score should always be in range."
        )
        severities.append("high")

    # ── Check 7: rubric weight validity ──────────────────────────────────────
    for criterion in rubric:
        if criterion.weight <= 0:
            flags.append("invalid_rubric_weight")
            reasons.append(
                f"Criterion '{criterion.name}' has weight {criterion.weight} ≤ 0, "
                "which is invalid."
            )
            severities.append("medium")

    # ── Aggregate ─────────────────────────────────────────────────────────────
    if not flags:
        return GuardrailResult(passed=True, severity=None, flags=[], reasons=[])

    top_severity = _top_severity(severities)
    return GuardrailResult(
        passed=False,
        severity=top_severity,
        flags=flags,
        reasons=reasons,
    )


def validate_score_evidence(evidence: ScoreEvidence) -> GuardrailResult:
    """Validate a single ``ScoreEvidence`` record in isolation.

    Useful for unit-testing individual score records.

    Args:
        evidence: A ``ScoreEvidence`` to validate.

    Returns:
        A :class:`~app.models.guardrail_models.GuardrailResult`.
    """
    flags: list[str] = []
    reasons: list[str] = []
    severities: list[str] = []
    _validate_evidence(evidence, flags, reasons, severities)
    if not flags:
        return GuardrailResult(passed=True, severity=None, flags=[], reasons=[])
    return GuardrailResult(
        passed=False,
        severity=_top_severity(severities),
        flags=flags,
        reasons=reasons,
    )


# ── Private helpers ───────────────────────────────────────────────────────────

_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}


def _top_severity(severities: list[str]) -> str:
    return max(severities, key=lambda s: _SEVERITY_RANK.get(s, 0))


def _validate_evidence(
    evidence: ScoreEvidence,
    flags: list[str],
    reasons: list[str],
    severities: list[str],
) -> None:
    """Append validation findings for a single ScoreEvidence into the mutable lists."""
    crit = evidence.criterion

    # Score range
    if evidence.score < SCORE_MIN or evidence.score > SCORE_MAX:
        flags.append("score_out_of_range")
        reasons.append(
            f"Criterion '{crit}': score {evidence.score} is outside [{SCORE_MIN}, {SCORE_MAX}]. "
            "Possible score manipulation."
        )
        severities.append("high")

    # Non-empty evidence
    if not evidence.evidence or not evidence.evidence.strip():
        flags.append("empty_evidence")
        reasons.append(
            f"Criterion '{crit}': evidence field is empty — scores must be backed by evidence."
        )
        severities.append("high")

    # Non-empty reasoning
    if not evidence.reasoning or not evidence.reasoning.strip():
        flags.append("empty_reasoning")
        reasons.append(
            f"Criterion '{crit}': reasoning field is empty."
        )
        severities.append("low")

    # Suspicious manipulation phrases in evidence or reasoning
    combined_text = (
        (evidence.evidence or "").lower()
        + " "
        + (evidence.reasoning or "").lower()
    )
    for phrase in _SUSPICIOUS_EVIDENCE_PHRASES:
        if phrase in combined_text:
            flags.append("suspicious_evidence")
            reasons.append(
                f"Criterion '{crit}': evidence or reasoning contains suspicious phrase "
                f"'{phrase}' — possible injection attempt."
            )
            severities.append("high")
            break  # one flag per criterion is enough
