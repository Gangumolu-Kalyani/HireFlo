"""Fairness guardrail — detects prohibited attribute references in scoring evidence.

The recruitment scoring system must only use job-relevant information.

Prohibited attributes (must NOT be used for scoring decisions):
    - gender / sex / pronouns
    - age / date of birth / year of birth
    - nationality / citizenship / country of origin
    - religion / faith / religious affiliation
    - caste / ethnicity / race
    - marital status / family status
    - name (as a scoring signal)
    - college prestige / university ranking
    - physical appearance / photograph
    - disability (as a scoring signal against the candidate)
    - sexual orientation

SCOPE
-----
Phase 5 implements rule-based fairness checks.

This module inspects the *scoring evidence text* produced by the LLM and flags
any reference to prohibited attributes.  It does NOT attempt a full statistical
fairness model — that is left for a later phase.

A flagged result does not automatically reject a candidate.  The graph records
the flag and includes it in the HumanReview object so a human reviewer can
assess whether it affected the decision.

SCORING ISOLATION (already in place)
-------------------------------------
``score_candidate_service`` already omits ``name``, ``email``, and ``phone``
from the profile text sent to the LLM.  This fairness layer acts as an
additional audit check on the evidence strings the LLM *does* return.
"""

from __future__ import annotations

import re

from app.models.guardrail_models import GuardrailResult
from app.models.scoring import CandidateScore, ScoreEvidence

# ── Prohibited attribute patterns ─────────────────────────────────────────────
#
# Each entry: (compiled_pattern, human_readable_attribute_label)
#
# Patterns are matched case-insensitively against the evidence + reasoning text
# of each ScoreEvidence entry.

_PROHIBITED: list[tuple[re.Pattern[str], str]] = [
    # Gender / sex / pronouns
    (re.compile(r"\b(gender|male|female|woman|man|girl|boy|she/her|he/him|they/them|non.?binary|transgender|trans\b)", re.IGNORECASE), "gender"),
    # Age
    (re.compile(r"\b(age|aged|years?\s+old|born\s+in|date\s+of\s+birth|dob|young|old(?:er)?|senior|junior\s+in\s+age)\b", re.IGNORECASE), "age"),
    # Nationality / citizenship
    (re.compile(r"\b(nationality|national\s+origin|citizenship|citizen\s+of|country\s+of\s+origin|immigrant|migrant|foreigner|foreign\s+national|expat)\b", re.IGNORECASE), "nationality"),
    # Religion
    (re.compile(r"\b(religion|religious|faith|christian|muslim|hindu|buddhist|jewish|sikh|atheist|agnostic|mosque|church|temple|synagogue|gurdwara)\b", re.IGNORECASE), "religion"),
    # Caste / ethnicity / race
    (re.compile(r"\b(caste|ethnicity|ethnic|race|racial|tribe|indigenous|aboriginal)\b", re.IGNORECASE), "caste_or_ethnicity"),
    # Marital / family status
    (re.compile(r"\b(married|unmarried|single|divorced|widowed|marital\s+status|family\s+status|pregnant|maternity|paternity|parent(?:hood)?)\b", re.IGNORECASE), "marital_or_family_status"),
    # Physical appearance / disability used negatively
    (re.compile(r"\b(appearance|looks|attractive|disability|disabled|handicap|wheelchair)\b", re.IGNORECASE), "appearance_or_disability"),
    # Sexual orientation
    (re.compile(r"\b(sexual\s+orientation|homosexual|heterosexual|bisexual|lesbian|gay\b|queer)\b", re.IGNORECASE), "sexual_orientation"),
    # College prestige / university ranking
    (re.compile(r"\b(ivy\s+league|top\s+university|elite\s+university|prestigious\s+(university|college|school)|ranked\s+(university|school)|oxford|cambridge|harvard|mit|stanford)\b", re.IGNORECASE), "college_prestige"),
    # Name as scoring signal (distinguishable from legitimate name mentions)
    (re.compile(r"\b(the\s+candidate'?s?\s+name\s+(suggests?|implies?|indicates?|sounds?|is?\s+(foreign|local|typical|unusual))|sounds?\s+(foreign|ethnic|western|local|asian|indian|arabic))\b", re.IGNORECASE), "name_as_signal"),
]


def check_scoring_fairness(candidate_score: CandidateScore) -> GuardrailResult:
    """Check scoring evidence for references to prohibited attributes.

    Inspects the ``evidence`` and ``reasoning`` fields of every
    ``ScoreEvidence`` in the given ``CandidateScore``.

    Args:
        candidate_score: The scored result to audit.

    Returns:
        A :class:`~app.models.guardrail_models.GuardrailResult`.

        - ``passed``: True if no prohibited attributes were referenced.
        - ``flags``:  One flag per (criterion, attribute) pair found.
        - ``reasons``: Human-readable explanation for each flag.
        - ``severity``: ``"medium"`` when any prohibited attribute is found.

    Notes:
        A ``passed=False`` result does NOT automatically reject the candidate.
        The graph records the result in ``state["fairness_flags"]`` and the
        human reviewer sees it in the ``HumanReview`` object.
    """
    flags: list[str] = []
    reasons: list[str] = []

    for evidence in candidate_score.scores:
        _check_evidence(evidence, flags, reasons)

    if not flags:
        return GuardrailResult(passed=True, severity=None, flags=[], reasons=[])

    return GuardrailResult(
        passed=False,
        severity="medium",
        flags=flags,
        reasons=reasons,
    )


def check_evidence_text(text: str) -> GuardrailResult:
    """Check a single free-form evidence or reasoning string.

    Useful for unit-testing individual evidence snippets.

    Args:
        text: The evidence or reasoning text to inspect.

    Returns:
        A :class:`~app.models.guardrail_models.GuardrailResult`.
    """
    flags: list[str] = []
    reasons: list[str] = []

    for pattern, attribute_label in _PROHIBITED:
        if pattern.search(text):
            flags.append(f"prohibited_attribute:{attribute_label}")
            reasons.append(
                f"Evidence text references a prohibited attribute: '{attribute_label}'. "
                "Scoring must be based on job-relevant information only."
            )

    if not flags:
        return GuardrailResult(passed=True, severity=None, flags=[], reasons=[])
    return GuardrailResult(
        passed=False,
        severity="medium",
        flags=flags,
        reasons=reasons,
    )


# ── Private helpers ───────────────────────────────────────────────────────────


def _check_evidence(
    evidence: ScoreEvidence,
    flags: list[str],
    reasons: list[str],
) -> None:
    """Check a single ScoreEvidence for prohibited attributes."""
    combined = (
        (evidence.evidence or "")
        + " "
        + (evidence.reasoning or "")
    )

    for pattern, attribute_label in _PROHIBITED:
        if pattern.search(combined):
            flag = f"prohibited_attribute:{attribute_label}:{evidence.criterion}"
            if flag not in flags:
                flags.append(flag)
                reasons.append(
                    f"Criterion '{evidence.criterion}': evidence or reasoning references "
                    f"prohibited attribute '{attribute_label}'. "
                    "Recruitment scoring must be based on job-relevant information only."
                )
