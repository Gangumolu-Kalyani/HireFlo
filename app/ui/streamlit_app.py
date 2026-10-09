"""HireFlo — Streamlit Human Review UI  (Phase 6).

Entry point:
    streamlit run app/ui/streamlit_app.py

Architecture
------------
This module is the *presentation / controller* layer only.  All recruitment
logic lives in the Phase 4/5 backend:

    Streamlit UI
         │
         ▼
    runner.run_recruitment()
    runner.approve_interview()
    runner.reject_interview()
         │
         ▼
    LangGraph agent  →  Guardrails  →  Tools  →  LLM services

No LLM calls, scoring, or guardrail logic belongs here.

Module layout
-------------
_session        — session-state helpers
_sidebar        — sidebar configuration widgets
_input          — resume + JD input section
_profile        — candidate profile display
_score          — score + recommendation display
_evidence       — criterion-level evidence expanders
_guardrails     — guardrail + fairness warning panel
_proposal       — interview proposal + approve/reject buttons
_audit          — audit trail expander
_error          — safe error display (no stack traces to HR user)
main            — page assembly
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path
from typing import Any

# Ensure the project root (the directory containing 'app/') is on sys.path so
# that `from app.*` imports work regardless of which directory Streamlit is
# launched from (e.g. `streamlit run app/ui/streamlit_app.py` from any CWD).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from app.observability.logging_config import get_logger, setup_logging

# ── Production Logging ────────────────────────────────────────────────────────
setup_logging()
logger = get_logger(__name__)
logger.info("HireFlo application started")


# ── Constants ────────────────────────────────────────────────────────────────

# ── In-process rate limiting ──────────────────────────────────────────────────
# Each Streamlit session is one browser tab / user.  These limits prevent a
# single session from hammering the LLM backend with rapid evaluations.
#
# Implementation: session-scoped counters stored in st.session_state.
# No external state store (Redis, DB) is needed because the limit is per
# browser session.  This is a lightweight production guard, not a distributed
# rate limiter.
#
# For a production multi-user deployment, move to a shared rate limiter backed
# by a database or Redis.  That is documented as future work.
_RATE_LIMIT_MAX_EVALUATIONS: int = 10   # max evaluations per session
_RATE_LIMIT_WINDOW_SECONDS: int = 3600  # rolling window (1 hour)
_RATE_LIMIT_MIN_INTERVAL_SECONDS: int = 5  # minimum seconds between evaluations

_STATUSES_WITH_APPROVAL = {"PENDING_APPROVAL"}
_STATUSES_NO_APPROVAL = {
    "APPROVED",
    "REJECTED",
    "REJECTED_BY_THRESHOLD",
    "GUARDRAIL_BLOCKED",
    "FAILED",
}

_DEFAULT_INTERVIEW_WEEK = "2026-W41"

# ── Recommendation display helpers ────────────────────────────────────────────

_RECOMMENDATION_LABEL: dict[str, str] = {
    "strong_yes": "⭐ STRONG MATCH",
    "yes": "✅ MATCH",
    "maybe": "⚠️  BORDERLINE",
    "no": "❌ NOT RECOMMENDED",
}

_RECOMMENDATION_COLOR: dict[str, str] = {
    "strong_yes": "🟢",
    "yes": "🟢",
    "maybe": "🟡",
    "no": "🔴",
}

# =============================================================================
# SESSION STATE HELPERS
# =============================================================================


def _init_session() -> None:
    """Initialise all session-state keys with safe defaults (once per session)."""
    defaults: dict[str, Any] = {
        "thread_id": None,
        "current_state": None,
        "candidate_profile": None,
        "score": None,
        "human_review": None,
        "workflow_status": None,
        "evaluation_running": False,
        # Rate-limiting state — per browser session, never persisted.
        "_rate_eval_timestamps": [],   # list[float] — epoch timestamps of recent evals
    }
    for key, default in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default


def _reset_session() -> None:
    """Clear all session state to start a fresh evaluation."""
    for key in [
        "thread_id",
        "current_state",
        "candidate_profile",
        "score",
        "human_review",
        "workflow_status",
        "evaluation_running",
    ]:
        st.session_state.pop(key, None)
    _init_session()


# =============================================================================
# IN-PROCESS RATE LIMITING
# =============================================================================


def _check_rate_limit() -> tuple[bool, str]:
    """Check whether the current session is allowed to start a new evaluation.

    Returns
    -------
    allowed : bool
        True if the evaluation may proceed.
    reason : str
        Human-readable message explaining a rejection (empty when allowed).

    Implementation notes
    --------------------
    - Limits are per browser session (st.session_state) only.
    - No external state store is used.
    - Timestamps are pruned to the rolling window on each call so memory
      usage stays bounded.
    - This is a best-effort guard; a sophisticated attacker can bypass it by
      opening multiple browser tabs.  For a public multi-user deployment,
      replace or supplement with a server-side rate limiter.
    """
    now = time.monotonic()
    timestamps: list[float] = st.session_state.get("_rate_eval_timestamps", [])

    # Prune timestamps outside the rolling window.
    window_start = now - _RATE_LIMIT_WINDOW_SECONDS
    timestamps = [t for t in timestamps if t >= window_start]
    st.session_state["_rate_eval_timestamps"] = timestamps

    # Check minimum interval between consecutive evaluations.
    if timestamps:
        seconds_since_last = now - timestamps[-1]
        if seconds_since_last < _RATE_LIMIT_MIN_INTERVAL_SECONDS:
            wait = int(_RATE_LIMIT_MIN_INTERVAL_SECONDS - seconds_since_last) + 1
            return False, (
                f"Please wait {wait} second{'s' if wait != 1 else ''} before "
                "starting another evaluation."
            )

    # Check total evaluations within the rolling window.
    if len(timestamps) >= _RATE_LIMIT_MAX_EVALUATIONS:
        oldest = timestamps[0]
        reset_in = int(_RATE_LIMIT_WINDOW_SECONDS - (now - oldest)) + 1
        minutes = reset_in // 60
        seconds = reset_in % 60
        time_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
        return False, (
            f"You have reached the limit of {_RATE_LIMIT_MAX_EVALUATIONS} evaluations "
            f"per hour.  Please wait {time_str} before trying again."
        )

    return True, ""


def _record_evaluation_start() -> None:
    """Record the current time as an evaluation start in the rate-limit state."""
    now = time.monotonic()
    timestamps: list[float] = st.session_state.get("_rate_eval_timestamps", [])
    timestamps.append(now)
    st.session_state["_rate_eval_timestamps"] = timestamps

def _update_session_from_state(state: dict) -> None:
    """Sync session-state keys from a freshly returned graph state dict."""
    st.session_state["current_state"] = state
    st.session_state["workflow_status"] = state.get("final_status")

    if cp := state.get("candidate_profile"):
        st.session_state["candidate_profile"] = cp

    if cs := state.get("candidate_score"):
        st.session_state["score"] = cs

    if hr := state.get("human_review"):
        st.session_state["human_review"] = hr


# =============================================================================
# SIDEBAR
# =============================================================================


def _render_sidebar() -> tuple[str, float]:
    """Render sidebar configuration widgets.

    Returns
    -------
    interview_week : str
        ISO week string entered by the HR user.
    score_threshold_display : float
        Display-only; the real threshold lives in build_graph().
    """
    st.sidebar.title("⚙️  Configuration")
    st.sidebar.markdown("---")

    interview_week = st.sidebar.text_input(
        "Interview Week (ISO)",
        value=_DEFAULT_INTERVIEW_WEEK,
        help="e.g. 2026-W41  —  ISO 8601 week format",
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown("**Score Threshold**")
    st.sidebar.info("3.0 / 5.0  (configured in agent)")

    st.sidebar.markdown("---")
    st.sidebar.markdown("**Workflow Status**")
    status = st.session_state.get("workflow_status") or "—"
    _render_status_badge_sidebar(status)

    st.sidebar.markdown("---")
    st.sidebar.markdown("**About HireFlo**")
    st.sidebar.caption(
        "AI recruitment agent (LangGraph + OpenRouter).  "
        "Human approval required before any interview is confirmed."
    )

    return interview_week, 3.0


def _render_status_badge_sidebar(status: str) -> None:
    """Show a coloured status pill in the sidebar."""
    color_map = {
        "PENDING_APPROVAL": "⏳",
        "APPROVED": "✅",
        "REJECTED": "🚫",
        "REJECTED_BY_THRESHOLD": "📉",
        "GUARDRAIL_BLOCKED": "🛡️",
        "FAILED": "❌",
        "STARTED": "🔄",
        "PARSED": "🔄",
        "SCORED": "🔄",
        "AVAILABILITY_CHECKED": "🔄",
    }
    icon = color_map.get(status, "ℹ️")
    st.sidebar.markdown(f"{icon}  **{status}**")


# =============================================================================
# INPUT SECTION — resume + job description
# =============================================================================


def _render_input_section() -> tuple[str, str]:
    """Render resume and JD inputs.

    Returns ``(resume_text, job_description)`` — both may be empty strings
    if the user has not yet filled them in.  The caller validates before use.

    PDF upload uses the stdlib ``io`` module only — no extra dependencies.
    """
    st.header("📄 Candidate Resume")

    input_mode = st.radio(
        "Resume input method",
        ["Paste text", "Upload file (.txt or .pdf)"],
        horizontal=True,
        label_visibility="collapsed",
    )

    resume_text = ""

    if input_mode == "Paste text":
        resume_text = st.text_area(
            "Paste resume text here",
            height=200,
            placeholder="Alex Rivera\nalex.rivera@example.com\n\nSUMMARY\n...",
            key="resume_paste",
        )
    else:
        uploaded = st.file_uploader(
            "Upload resume",
            type=["txt", "pdf"],
            help="Plain text (.txt) or PDF (.pdf). Content is treated as untrusted data.",
            key="resume_upload",
        )
        if uploaded is not None:
            resume_text = _extract_text_from_upload(uploaded)
            if resume_text:
                with st.expander("Preview extracted text", expanded=False):
                    st.text(resume_text[:2000] + ("…" if len(resume_text) > 2000 else ""))
            else:
                st.error("⚠️  Could not extract text from the uploaded file.")

    st.header("📋 Job Description")
    job_description = st.text_area(
        "Enter the job description",
        height=150,
        placeholder="Senior Python Backend Engineer\n\nRequirements:\n- 4+ years Python...",
        key="jd_text",
    )

    return resume_text, job_description


def _extract_text_from_upload(uploaded_file: Any) -> str:
    """Extract plain text from an uploaded .txt or .pdf file.

    - .txt files are decoded as UTF-8 with error replacement.
    - .pdf files use a minimal line-based extraction from the raw bytes —
      no third-party document framework is required.

    The caller passes all extracted text through the existing Phase 5 guardrails;
    file content is treated as *untrusted data*.
    """
    name: str = uploaded_file.name.lower()
    raw: bytes = uploaded_file.read()

    if name.endswith(".txt"):
        return raw.decode("utf-8", errors="replace")

    if name.endswith(".pdf"):
        return _extract_pdf_text(raw)

    return raw.decode("utf-8", errors="replace")


def _extract_pdf_text(raw: bytes) -> str:
    """Minimal PDF text extraction without third-party libraries.

    Scans the raw PDF bytes for BT/ET (Begin/End Text) blocks and extracts
    printable ASCII strings from Tj / TJ operators.  This handles most
    text-based PDFs.  It does NOT handle:
    - scanned image PDFs (no OCR)
    - encrypted PDFs
    - PDFs with custom encoding maps

    For those cases the user is shown a fallback message prompting them to
    paste the resume text instead.
    """
    import re

    text_parts: list[str] = []
    try:
        # Decode bytes, ignoring non-latin-1 for pattern matching
        content = raw.decode("latin-1", errors="replace")

        # Extract string operands from Tj (single string) and TJ (array) operators
        # Pattern: (text)Tj  or  [(text)...]TJ
        tj_pattern = re.compile(r"\(([^)]*)\)\s*Tj", re.DOTALL)
        tj_array_pattern = re.compile(r"\(([^)]*)\)", re.DOTALL)

        # Find BT...ET blocks
        bt_et_pattern = re.compile(r"BT(.*?)ET", re.DOTALL)
        for block in bt_et_pattern.finditer(content):
            block_text = block.group(1)
            # Try Tj first, then fall back to any parenthesised strings
            parts = tj_pattern.findall(block_text)
            if not parts:
                parts = tj_array_pattern.findall(block_text)
            for part in parts:
                # Unescape common PDF escape sequences
                part = part.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")
                part = part.replace("\\(", "(").replace("\\)", ")")
                # Filter to printable ASCII + newlines
                cleaned = "".join(
                    c for c in part if c.isprintable() or c in "\n\r\t"
                )
                if cleaned.strip():
                    text_parts.append(cleaned)

    except Exception:  # noqa: BLE001
        logger.warning("PDF extraction failed; returning empty string")
        return ""

    extracted = "\n".join(text_parts).strip()
    if not extracted:
        return ""
    return extracted


# =============================================================================
# EVALUATION — calls runner
# =============================================================================


def _validate_inputs(resume_text: str, job_description: str) -> list[str]:
    """Return a list of validation error messages (empty = valid)."""
    errors: list[str] = []
    if not resume_text or not resume_text.strip():
        errors.append("Resume text is required.")
    if not job_description or not job_description.strip():
        errors.append("Job description is required.")
    return errors


def _run_evaluation(
    resume_text: str,
    job_description: str,
    interview_week: str,
) -> None:
    """Call the Phase 4/5 runner and store results in session state.

    This function imports the runner lazily to avoid module-level side effects
    when Streamlit reloads the script.
    """
    from app.agent.runner import run_recruitment

    # Build a default rubric from the job description keywords.
    # In a production app this would be configurable via the UI; for now we
    # use a sensible default covering common engineering dimensions.
    rubric = _build_default_rubric()

    try:
        thread_id, state = run_recruitment(
            resume_text,
            rubric,
            job_description=job_description,
            interview_week=interview_week,
        )
        st.session_state["thread_id"] = thread_id
        _update_session_from_state(state)

    except Exception as exc:
        logger.exception("run_recruitment raised")
        st.session_state["workflow_status"] = "FAILED"
        st.session_state["current_state"] = {
            "final_status": "FAILED",
            "errors": [_safe_error_message(exc)],
        }


def _build_default_rubric() -> list:
    """Return a sensible default rubric for a software engineering role.

    This rubric is used when the user has not configured a custom one.
    It covers the five most common dimensions in engineering JDs.
    """
    from app.models import ScoringCriterion

    return [
        ScoringCriterion(name="Programming Skills", description="Proficiency in core programming languages and paradigms.", weight=0.25),
        ScoringCriterion(name="Backend / API Development", description="Experience building REST APIs and backend services.", weight=0.25),
        ScoringCriterion(name="System Design", description="Ability to design scalable, maintainable systems.", weight=0.20),
        ScoringCriterion(name="Tooling & DevOps", description="Docker, CI/CD, cloud platforms, testing.", weight=0.15),
        ScoringCriterion(name="Projects & Impact", description="Demonstrated work and measurable impact.", weight=0.15),
    ]


def _safe_error_message(exc: Exception) -> str:
    """Return a user-safe error message that never exposes stack traces or secrets."""
    # Log full details for developers; show a generic message to HR users.
    logger.error("Agent error: %s\n%s", exc, traceback.format_exc())
    return f"Evaluation encountered an error: {type(exc).__name__}."


# =============================================================================
# CANDIDATE PROFILE DISPLAY
# =============================================================================


def _render_candidate_profile(profile: dict) -> None:
    """Render the parsed candidate profile."""
    st.header("👤 Candidate Profile")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**Name:** {profile.get('name', '—')}")
        st.markdown(f"**Email:** {profile.get('email') or '—'}")
        st.markdown(f"**Phone:** {profile.get('phone') or '—'}")
        yoe = profile.get("years_of_experience", 0)
        st.markdown(f"**Experience:** {yoe} year{'s' if yoe != 1 else ''}")

    with col2:
        skills = profile.get("skills") or []
        if skills:
            st.markdown("**Skills:**")
            st.markdown(", ".join(f"`{s}`" for s in skills))
        else:
            st.markdown("**Skills:** —")

        education = profile.get("education") or []
        if education:
            st.markdown("**Education:**")
            for edu in education:
                st.markdown(f"- {edu}")

    # Experience and projects in expanders to keep the page compact
    experience = profile.get("experience") or []
    if experience:
        with st.expander("Work Experience", expanded=False):
            for role in experience:
                st.markdown(f"- {role}")

    projects = profile.get("projects") or []
    if projects:
        with st.expander("Projects", expanded=False):
            for proj in projects:
                st.markdown(f"- {proj}")

    summary = profile.get("summary", "")
    if summary:
        with st.expander("Summary", expanded=False):
            st.markdown(summary)


# =============================================================================
# SCORE & RECOMMENDATION DISPLAY
# =============================================================================


def _render_score_section(score: dict) -> None:
    """Render overall score metrics and recommendation tier."""
    st.header("📊 Candidate Evaluation")

    weighted = score.get("weighted_score", 0.0)
    rec = score.get("recommendation", "")
    rec_label = _RECOMMENDATION_LABEL.get(rec, rec.upper())
    rec_icon = _RECOMMENDATION_COLOR.get(rec, "⚪")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Overall Score", f"{weighted:.1f} / 5.0")
    with col2:
        st.metric("Recommendation", rec_label)
    with col3:
        st.metric("Candidate", score.get("candidate_name", "—"))

    # Visual score bar
    pct = int((weighted / 5.0) * 100)
    st.progress(pct, text=f"{rec_icon} Score: {weighted:.2f}/5.00  ({pct}%)")


def _render_evidence_section(score: dict) -> None:
    """Render per-criterion score evidence in expandable panels."""
    st.header("🔍 Criterion Evaluation")

    criteria_scores: list[dict] = score.get("scores", [])
    if not criteria_scores:
        st.info("No criterion scores available.")
        return

    for item in criteria_scores:
        criterion = item.get("criterion", "?")
        s = item.get("score", 0)
        evidence = item.get("evidence", "")
        reasoning = item.get("reasoning", "")

        # Score colour coding
        if s >= 4.0:
            score_tag = f"🟢 {s:.1f}/5"
        elif s >= 2.5:
            score_tag = f"🟡 {s:.1f}/5"
        else:
            score_tag = f"🔴 {s:.1f}/5"

        with st.expander(f"{criterion}  —  {score_tag}", expanded=False):
            if evidence:
                st.markdown("**Evidence from resume:**")
                st.markdown(f"> {evidence}")
            if reasoning:
                st.markdown("**Reasoning:**")
                st.markdown(reasoning)
            if not evidence and not reasoning:
                st.caption("No evidence recorded.")


# =============================================================================
# GUARDRAILS DISPLAY
# =============================================================================


def _render_guardrails_section(state: dict) -> None:
    """Render guardrail and fairness check results."""
    st.header("🛡️  Security & Guardrails")

    guardrail_flags: list[str] = state.get("guardrail_flags") or []
    fairness_flags: list[str] = state.get("fairness_flags") or []
    final_status = state.get("final_status", "")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("**Prompt Injection**")
        inj_flags = [f for f in guardrail_flags if "injection" in f.lower() or "input" in f.lower()]
        if final_status == "GUARDRAIL_BLOCKED":
            st.error("🛡️  BLOCKED")
        elif inj_flags:
            st.warning(f"⚠️  FLAGGED\n\n{'; '.join(inj_flags)}")
        else:
            st.success("✅ PASSED")

    with col2:
        st.markdown("**Output Validation**")
        out_flags = [f for f in guardrail_flags if "output" in f.lower() or "score" in f.lower() or "evidence" in f.lower()]
        if out_flags:
            st.warning(f"⚠️  FLAGGED\n\n{'; '.join(out_flags)}")
        else:
            st.success("✅ PASSED")

    with col3:
        st.markdown("**Fairness Check**")
        if fairness_flags:
            st.warning("⚠️  REVIEW REQUIRED")
        else:
            st.success("✅ PASSED")

    # Detailed flags
    if guardrail_flags or fairness_flags:
        with st.expander("View all guardrail flags", expanded=False):
            if guardrail_flags:
                st.markdown("**Security flags:**")
                for flag in guardrail_flags:
                    st.markdown(f"- `{flag}`")
            if fairness_flags:
                st.markdown("**Fairness flags:**")
                for flag in fairness_flags:
                    st.warning(f"⚠️  FAIRNESS REVIEW REQUIRED\n\n**Flag:** `{flag}`\n\n**Reason:** Scoring evidence may contain a reference to a protected demographic attribute. The human reviewer should verify that this did not influence the recommendation.")

    # Full block display
    if final_status == "GUARDRAIL_BLOCKED":
        st.error(
            "🛡️  **EVALUATION BLOCKED BY SECURITY GUARDRAILS**\n\n"
            "The evaluation was stopped because the input or output triggered a "
            "high-severity security rule.  This is a safety measure to protect the "
            "integrity of the recruitment process.\n\n"
            f"**Flags recorded:** {', '.join(guardrail_flags) or 'see logs'}"
        )


# =============================================================================
# BLOCKED / LOW SCORE STATE BANNERS
# =============================================================================


def _render_guardrail_blocked_banner(state: dict) -> None:
    """Full-page banner for GUARDRAIL_BLOCKED status."""
    flags = state.get("guardrail_flags") or []
    st.error(
        "## 🛡️  Evaluation Blocked by Security Guardrails\n\n"
        "The recruitment workflow was stopped before completion because the submitted "
        "content triggered a high-severity security rule.\n\n"
        "**What this means:**\n"
        "- The resume or job description may contain content that attempts to manipulate "
        "the AI agent.\n"
        "- No candidate data was scored and no interview was proposed.\n"
        "- This is expected and correct behaviour — the system is working as designed.\n\n"
        "**Flags recorded:**\n"
        + ("\n".join(f"- `{f}`" for f in flags) if flags else "- (see audit trail)")
        + "\n\n"
        "**What to do:** Review the submitted text and remove any content that resembles "
        "instructions or commands.  Then submit again."
    )


def _render_rejected_by_threshold_banner(state: dict, score: dict | None) -> None:
    """Banner for REJECTED_BY_THRESHOLD status."""
    st.warning(
        "## 📉  Candidate Did Not Meet the Evaluation Threshold\n\n"
        "The candidate's weighted score was below the configured threshold (3.0 / 5.0). "
        "No interview proposal was generated."
    )
    if score:
        weighted = score.get("weighted_score", 0.0)
        rec = score.get("recommendation", "")
        st.metric("Score", f"{weighted:.2f} / 5.0")
        st.metric("Recommendation", _RECOMMENDATION_LABEL.get(rec, rec))


def _render_failed_banner(state: dict) -> None:
    """Banner for FAILED status — safe, no stack traces."""
    errors = state.get("errors") or []
    # Show only the first error; never expose tracebacks or secrets.
    safe_errors = [e for e in errors if "api" not in e.lower() and "key" not in e.lower()]
    st.error(
        "## ❌  Evaluation Failed\n\n"
        "The recruitment workflow encountered an unexpected error and could not complete.\n\n"
        "**Details:**\n"
        + ("\n".join(f"- {e}" for e in safe_errors[:3]) if safe_errors else "- An internal error occurred.")
    )


# =============================================================================
# INTERVIEW PROPOSAL & HUMAN REVIEW
# =============================================================================


def _render_human_review(human_review: dict) -> None:
    """Render the full HumanReview panel — candidate, score, evidence, proposal."""
    st.header("👩‍💼 Human Review — Interview Proposal")

    # Score summary
    score_val = human_review.get("score", 0.0)
    rec = human_review.get("recommendation", "")
    rec_label = _RECOMMENDATION_LABEL.get(rec, rec.upper())

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Candidate", human_review.get("candidate", "—"))
        st.metric("Overall Score", f"{score_val:.1f} / 5.0")
    with col2:
        st.metric("Recommendation", rec_label)
        st.metric("Approval Required", "Yes")

    # Evidence
    evidence: list[dict] = human_review.get("evidence") or []
    if evidence:
        with st.expander("📋 Criterion Evidence", expanded=True):
            for item in evidence:
                criterion = item.get("criterion", "?")
                s = item.get("score", 0)
                ev = item.get("evidence", "")
                rsn = item.get("reasoning", "")
                st.markdown(f"**{criterion}**  —  `{s:.1f}/5`")
                if ev:
                    st.markdown(f"> {ev}")
                if rsn:
                    st.caption(f"Reasoning: {rsn}")
                st.markdown("---")

    # Availability and proposed slot
    avail: list[str] = human_review.get("availability") or []
    proposed = human_review.get("proposed_slot", "")

    col3, col4 = st.columns(2)
    with col3:
        st.markdown("**Available Slots:**")
        if avail:
            for slot in avail:
                icon = "📅" if slot == proposed else "🕐"
                st.markdown(f"{icon} {slot}")
        else:
            st.caption("No availability data.")
    with col4:
        st.markdown("**Proposed Slot:**")
        if proposed:
            st.info(f"📅  **{proposed}**")
        else:
            st.caption("No slot proposed.")

    # Guardrail & fairness flags within review
    gr_flags: list[str] = human_review.get("guardrail_flags") or []
    fa_flags: list[str] = human_review.get("fairness_flags") or []

    if gr_flags or fa_flags:
        with st.expander("⚠️  Flags for Reviewer Attention", expanded=True):
            if gr_flags:
                st.warning("**Security / Guardrail Flags:**")
                for f in gr_flags:
                    st.markdown(f"- `{f}`")
            if fa_flags:
                st.warning(
                    "**Fairness Flags — Reviewer must assess whether protected "
                    "attributes influenced the score:**"
                )
                for f in fa_flags:
                    st.markdown(f"- `{f}`")
    else:
        st.success("✅ No guardrail or fairness flags.")


def _render_proposal_section(state: dict) -> None:
    """Render proposal info and approve/reject buttons."""
    proposal: dict = state.get("interview_proposal") or {}
    final_status = state.get("final_status", "")

    st.header("📅 Interview Proposal")

    if not proposal and final_status not in _STATUSES_WITH_APPROVAL:
        st.info("No interview proposal available for this evaluation.")
        return

    if proposal:
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**Candidate:** {proposal.get('candidate', '—')}")
            st.markdown(f"**Proposed Slot:** {proposal.get('slot', '—')}")
        with col2:
            st.markdown(f"**Status:** `{proposal.get('status', '—')}`")
            if ptime := proposal.get("proposed_at"):
                st.caption(f"Proposed at: {ptime[:19]}")

    _render_approval_controls(state)


def _render_approval_controls(state: dict) -> None:
    """Render APPROVE / REJECT buttons or final decision banners."""
    final_status = state.get("final_status", "")
    thread_id = st.session_state.get("thread_id")

    if final_status == "APPROVED":
        st.success("## ✅  Interview Approved\n\nThe interview has been approved. The recruiter can proceed with scheduling.")
        return

    if final_status == "REJECTED":
        st.error("## 🚫  Interview Rejected\n\nThe candidate has been rejected at the human review stage.")
        return

    if final_status not in _STATUSES_WITH_APPROVAL:
        return

    # PENDING_APPROVAL — show buttons
    st.markdown("---")
    st.markdown("### ⚖️  Decision Required")
    st.info(
        "👆 The AI agent has completed its evaluation.  **You must review the information above "
        "and make a decision.**  No interview will be scheduled automatically."
    )

    col_approve, col_reject = st.columns(2)

    with col_approve:
        if st.button(
            "✅  APPROVE INTERVIEW",
            type="primary",
            use_container_width=True,
            key="btn_approve",
        ):
            _handle_approve(thread_id)

    with col_reject:
        if st.button(
            "🚫  REJECT INTERVIEW",
            type="secondary",
            use_container_width=True,
            key="btn_reject",
        ):
            _handle_reject(thread_id)


def _handle_approve(thread_id: str | None) -> None:
    """Call approve_interview and refresh session state."""
    if not thread_id:
        st.error("No active thread — cannot approve.")
        return
    try:
        from app.agent.runner import approve_interview

        with st.spinner("Processing approval…"):
            final_state = approve_interview(thread_id)
        _update_session_from_state(final_state)
        st.rerun()
    except Exception as exc:
        logger.exception("approve_interview error")
        st.error(f"Approval failed: {type(exc).__name__}.")


def _handle_reject(thread_id: str | None) -> None:
    """Call reject_interview and refresh session state."""
    if not thread_id:
        st.error("No active thread — cannot reject.")
        return
    try:
        from app.agent.runner import reject_interview

        with st.spinner("Processing rejection…"):
            final_state = reject_interview(thread_id)
        _update_session_from_state(final_state)
        st.rerun()
    except Exception as exc:
        logger.exception("reject_interview error")
        st.error(f"Rejection failed: {type(exc).__name__}.")


# =============================================================================
# AUDIT TRAIL
# =============================================================================


def _render_audit_trail(state: dict) -> None:
    """Render the operational audit trail from graph trajectory.

    Only operational events are shown (node name, status, safe detail).
    Chain-of-thought and LLM reasoning are never included.
    """
    trajectory: list[dict] = state.get("trajectory") or []
    if not trajectory:
        return

    with st.expander("🗒️  Audit Trail", expanded=False):
        st.caption(
            "Operational events only. No LLM chain-of-thought or internal "
            "reasoning is shown here."
        )
        for i, entry in enumerate(trajectory, start=1):
            node = entry.get("node", "?")
            status = entry.get("status", "?")
            detail = entry.get("detail", "")
            step = entry.get("step", i)

            # Emoji per status
            icon = {
                "entered": "▶️",
                "completed": "✅",
                "interrupted": "⏸️",
                "resumed": "▶️",
                "blocked": "🛡️",
                "error": "❌",
            }.get(status, "ℹ️")

            label = f"{icon}  Step {step} — `{node}` — **{status.upper()}**"
            if detail:
                label += f"\n\n   _{detail}_"
            st.markdown(f"{i}. {label}")


# =============================================================================
# PAGE ASSEMBLY — main()
# =============================================================================


def main() -> None:
    """Assemble and render the full HireFlo dashboard."""
    st.set_page_config(
        page_title="HireFlo — AI Recruitment Agent",
        page_icon="💼",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    _init_session()

    # ── Header ────────────────────────────────────────────────────────────────
    st.title("💼 HireFlo — AI Recruitment Agent")
    st.markdown(
        "_Human-in-the-loop recruitment powered by LangGraph.  "
        "No interview is confirmed without your approval._"
    )
    st.markdown("---")

    # ── Sidebar ───────────────────────────────────────────────────────────────
    interview_week, _ = _render_sidebar()

    # ── Input section ─────────────────────────────────────────────────────────
    resume_text, job_description = _render_input_section()

    st.markdown("---")

    # ── Start evaluation button ───────────────────────────────────────────────
    col_btn, col_reset = st.columns([3, 1])
    with col_btn:
        start_clicked = st.button(
            "🚀  Start Candidate Evaluation",
            type="primary",
            use_container_width=True,
            key="btn_start",
        )
    with col_reset:
        reset_clicked = st.button(
            "🔄  New Evaluation",
            type="secondary",
            use_container_width=True,
            key="btn_reset",
        )

    if reset_clicked:
        _reset_session()
        st.rerun()

    if start_clicked:
        validation_errors = _validate_inputs(resume_text, job_description)
        if validation_errors:
            for err in validation_errors:
                st.error(f"⚠️  {err}")
        else:
            # Rate-limit check — per session, in-process guard.
            allowed, rate_reason = _check_rate_limit()
            if not allowed:
                st.warning(f"⏳  {rate_reason}")
            else:
                _record_evaluation_start()
                _reset_session()
                with st.spinner("Running candidate evaluation…  (this may take 10–30 seconds)"):
                    _run_evaluation(resume_text, job_description, interview_week)
                st.rerun()

    # ── Results area ──────────────────────────────────────────────────────────
    current_state: dict | None = st.session_state.get("current_state")
    if not current_state:
        st.info("Enter a resume and job description above, then click **Start Candidate Evaluation**.")
        return

    final_status: str = current_state.get("final_status", "")
    profile: dict | None = st.session_state.get("candidate_profile")
    score: dict | None = st.session_state.get("score")
    human_review: dict | None = st.session_state.get("human_review")

    # ── GUARDRAIL_BLOCKED ─────────────────────────────────────────────────────
    if final_status == "GUARDRAIL_BLOCKED":
        _render_guardrail_blocked_banner(current_state)
        _render_guardrails_section(current_state)
        _render_audit_trail(current_state)
        return

    # ── FAILED ────────────────────────────────────────────────────────────────
    if final_status == "FAILED":
        _render_failed_banner(current_state)
        _render_audit_trail(current_state)
        return

    # ── Candidate profile (shown for all non-blocked states) ─────────────────
    if profile:
        _render_candidate_profile(profile)
        st.markdown("---")

    # ── REJECTED_BY_THRESHOLD ─────────────────────────────────────────────────
    if final_status == "REJECTED_BY_THRESHOLD":
        _render_rejected_by_threshold_banner(current_state, score)
        if score:
            _render_evidence_section(score)
        _render_guardrails_section(current_state)
        _render_audit_trail(current_state)
        return

    # ── Score & evidence (for states that made it past threshold) ─────────────
    if score:
        _render_score_section(score)
        st.markdown("---")
        _render_evidence_section(score)
        st.markdown("---")

    # ── Guardrails (always shown if there is any result) ─────────────────────
    _render_guardrails_section(current_state)
    st.markdown("---")

    # ── Human review panel (PENDING_APPROVAL, APPROVED, REJECTED) ─────────────
    if human_review:
        _render_human_review(human_review)
        st.markdown("---")

    # ── Proposal + approval controls ─────────────────────────────────────────
    _render_proposal_section(current_state)
    st.markdown("---")

    # ── Audit trail ───────────────────────────────────────────────────────────
    _render_audit_trail(current_state)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
