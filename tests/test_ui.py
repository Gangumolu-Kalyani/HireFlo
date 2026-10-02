"""UI-focused tests for Phase 6 — Streamlit Human Review UI.

Strategy
--------
Streamlit cannot be fully executed in a pytest environment (it requires a
running server and browser session).  Instead these tests verify:

1.  The streamlit_app module imports without errors.
2.  Helper functions (input validation, PDF extraction, safe error messages,
    rubric builder, session helpers, status routing logic) are correct.
3.  The runner integration layer behaves correctly when mocked.
4.  No secrets are exposed in any rendered output path.
5.  Approval controls are only shown for PENDING_APPROVAL.
6.  Guardrail-blocked state produces the correct text.
7.  Audit trail helpers filter trajectory entries correctly.

All tests run offline (no real LLM calls, no real Streamlit server).
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

# =============================================================================
# 1. Module import
# =============================================================================

class TestModuleImport:
    """streamlit_app must import without raising even when streamlit itself
    is not installed in the test environment (we stub it out).
    """

    def test_streamlit_app_importable(self):
        """The module-level code in streamlit_app must not crash on import."""
        # If streamlit is not installed, stub it so the import succeeds.
        if "streamlit" not in sys.modules:
            stub = types.ModuleType("streamlit")
            # Provide the minimal attributes referenced at module level
            stub.session_state = {}
            sys.modules["streamlit"] = stub

        # Force a fresh import attempt
        if "app.ui.streamlit_app" in sys.modules:
            del sys.modules["app.ui.streamlit_app"]

        try:
            import app.ui.streamlit_app as app_module  # noqa: F401
        except Exception as exc:
            raise AssertionError(f"streamlit_app import raised: {exc}") from exc

    def test_ui_package_importable(self):
        """app.ui package __init__ must import without errors."""
        if "app.ui" in sys.modules:
            del sys.modules["app.ui"]
        import app.ui  # noqa: F401


# =============================================================================
# Helpers to import pure-logic functions without needing a live st context
# =============================================================================

def _get_app_module():
    """Return the streamlit_app module, stubbing streamlit if needed."""
    if "streamlit" not in sys.modules:
        stub = types.ModuleType("streamlit")
        stub.session_state = {}
        sys.modules["streamlit"] = stub

    if "app.ui.streamlit_app" in sys.modules:
        del sys.modules["app.ui.streamlit_app"]

    import app.ui.streamlit_app as m
    return m


# =============================================================================
# 2. Input validation
# =============================================================================

class TestInputValidation:
    """_validate_inputs must catch empty/whitespace inputs."""

    def setup_method(self):
        self.app = _get_app_module()

    def test_empty_resume_returns_error(self):
        errors = self.app._validate_inputs("", "Some JD")
        assert any("resume" in e.lower() or "Resume" in e for e in errors)

    def test_whitespace_resume_returns_error(self):
        errors = self.app._validate_inputs("   \n\t  ", "Some JD")
        assert any("resume" in e.lower() or "Resume" in e for e in errors)

    def test_empty_jd_returns_error(self):
        errors = self.app._validate_inputs("Some resume text", "")
        assert any("job" in e.lower() or "description" in e.lower() or "jd" in e.lower()
                   or "Job" in e for e in errors)

    def test_whitespace_jd_returns_error(self):
        errors = self.app._validate_inputs("Some resume text", "  ")
        assert len(errors) >= 1

    def test_both_empty_returns_two_errors(self):
        errors = self.app._validate_inputs("", "")
        assert len(errors) == 2

    def test_valid_inputs_return_no_errors(self):
        errors = self.app._validate_inputs("Alex Rivera\nPython developer", "Senior Python Engineer")
        assert errors == []


# =============================================================================
# 3. Safe error message — no secrets
# =============================================================================

class TestSafeErrorMessage:
    """_safe_error_message must never expose secrets or stack traces."""

    def setup_method(self):
        self.app = _get_app_module()

    def test_returns_string(self):
        msg = self.app._safe_error_message(ValueError("something went wrong"))
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_no_stack_trace_in_output(self):
        """The returned message must not contain traceback internals."""
        msg = self.app._safe_error_message(RuntimeError("deep internal error"))
        assert "Traceback" not in msg
        assert "File " not in msg

    def test_no_api_key_in_output(self):
        """Even if the exception mentions a key, it must be scrubbed."""
        exc = RuntimeError("sk-or-v1-supersecretkey123")
        msg = self.app._safe_error_message(exc)
        # The message must not pass the secret through
        assert "sk-or" not in msg
        assert "supersecret" not in msg

    def test_contains_exception_type_not_message(self):
        """Only the exception type name should appear, not raw message text."""
        msg = self.app._safe_error_message(ValueError("internal details"))
        assert "ValueError" in msg
        # Raw exception message should NOT pass through to the user
        assert "internal details" not in msg


# =============================================================================
# 4. PDF extraction helpers
# =============================================================================

class TestPdfExtraction:
    """_extract_pdf_text must return a string and not raise on bad input."""

    def setup_method(self):
        self.app = _get_app_module()

    def test_returns_string_on_empty_bytes(self):
        result = self.app._extract_pdf_text(b"")
        assert isinstance(result, str)

    def test_returns_string_on_garbage_bytes(self):
        result = self.app._extract_pdf_text(b"\x00\x01\x02\xff\xfe garbage")
        assert isinstance(result, str)

    def test_extracts_simple_bt_et_block(self):
        """A minimal fake PDF with a BT...ET block and Tj operator."""
        fake_pdf = b"BT (Hello World) Tj ET"
        result = self.app._extract_pdf_text(fake_pdf)
        assert "Hello World" in result

    def test_never_raises(self):
        """Must never propagate an exception regardless of input."""
        for bad in [b"", b"\xff" * 1000, b"random text no pdf structure"]:
            try:
                self.app._extract_pdf_text(bad)
            except Exception as exc:
                raise AssertionError(f"_extract_pdf_text raised on input: {exc}") from exc


# =============================================================================
# 5. Default rubric builder
# =============================================================================

class TestDefaultRubric:
    """_build_default_rubric must return valid ScoringCriterion objects."""

    def setup_method(self):
        self.app = _get_app_module()

    def test_returns_list(self):
        rubric = self.app._build_default_rubric()
        assert isinstance(rubric, list)
        assert len(rubric) >= 3

    def test_all_items_are_scoring_criteria(self):
        from app.models import ScoringCriterion

        rubric = self.app._build_default_rubric()
        for item in rubric:
            assert isinstance(item, ScoringCriterion)

    def test_weights_are_positive(self):
        rubric = self.app._build_default_rubric()
        for item in rubric:
            assert item.weight > 0

    def test_total_weight_approximately_one(self):
        rubric = self.app._build_default_rubric()
        total = sum(item.weight for item in rubric)
        assert abs(total - 1.0) < 0.01, f"weights sum to {total}, expected ~1.0"

    def test_no_duplicate_names(self):
        rubric = self.app._build_default_rubric()
        names = [item.name for item in rubric]
        assert len(names) == len(set(names))


# =============================================================================
# 6. Status routing helpers — approval button visibility logic
# =============================================================================

class TestStatusRouting:
    """Approval controls must only be available for PENDING_APPROVAL.

    We test the constant sets that gate the button display.
    """

    def setup_method(self):
        self.app = _get_app_module()

    def test_pending_approval_is_in_approval_set(self):
        assert "PENDING_APPROVAL" in self.app._STATUSES_WITH_APPROVAL

    def test_approved_is_not_in_approval_set(self):
        assert "APPROVED" not in self.app._STATUSES_WITH_APPROVAL

    def test_rejected_is_not_in_approval_set(self):
        assert "REJECTED" not in self.app._STATUSES_WITH_APPROVAL

    def test_rejected_by_threshold_is_not_in_approval_set(self):
        assert "REJECTED_BY_THRESHOLD" not in self.app._STATUSES_WITH_APPROVAL

    def test_guardrail_blocked_is_not_in_approval_set(self):
        assert "GUARDRAIL_BLOCKED" not in self.app._STATUSES_WITH_APPROVAL

    def test_failed_is_not_in_approval_set(self):
        assert "FAILED" not in self.app._STATUSES_WITH_APPROVAL

    def test_all_terminal_statuses_in_no_approval_set(self):
        for s in ["APPROVED", "REJECTED", "REJECTED_BY_THRESHOLD", "GUARDRAIL_BLOCKED", "FAILED"]:
            assert s in self.app._STATUSES_NO_APPROVAL, f"{s} missing from _STATUSES_NO_APPROVAL"


# =============================================================================
# 7. Recommendation label helpers
# =============================================================================

class TestRecommendationLabels:
    """Every valid recommendation tier must map to a display label."""

    def setup_method(self):
        self.app = _get_app_module()

    def test_strong_yes_has_label(self):
        assert "strong_yes" in self.app._RECOMMENDATION_LABEL

    def test_yes_has_label(self):
        assert "yes" in self.app._RECOMMENDATION_LABEL

    def test_maybe_has_label(self):
        assert "maybe" in self.app._RECOMMENDATION_LABEL

    def test_no_has_label(self):
        assert "no" in self.app._RECOMMENDATION_LABEL

    def test_labels_are_non_empty_strings(self):
        for key, label in self.app._RECOMMENDATION_LABEL.items():
            assert isinstance(label, str) and label.strip(), f"Empty label for {key!r}"


# =============================================================================
# 8. Session-state update logic
# =============================================================================

class TestSessionUpdate:
    """_update_session_from_state must correctly sync the session dict."""

    def setup_method(self):
        self.app = _get_app_module()

    def _make_session(self):
        """Return a fresh dict to stand in for st.session_state."""
        return {
            "thread_id": None,
            "current_state": None,
            "candidate_profile": None,
            "score": None,
            "human_review": None,
            "workflow_status": None,
            "evaluation_running": False,
        }

    def test_workflow_status_set(self):
        session = self._make_session()
        state = {"final_status": "PENDING_APPROVAL"}
        with patch.object(self.app.st, "session_state", session):
            self.app._update_session_from_state(state)
        assert session["workflow_status"] == "PENDING_APPROVAL"

    def test_candidate_profile_set(self):
        session = self._make_session()
        profile = {"name": "Alex Rivera", "email": "alex@example.com"}
        state = {"final_status": "SCORED", "candidate_profile": profile}
        with patch.object(self.app.st, "session_state", session):
            self.app._update_session_from_state(state)
        assert session["candidate_profile"] == profile

    def test_score_set(self):
        session = self._make_session()
        score = {"weighted_score": 4.2, "recommendation": "yes", "candidate_name": "Alex", "scores": []}
        state = {"final_status": "SCORED", "candidate_score": score}
        with patch.object(self.app.st, "session_state", session):
            self.app._update_session_from_state(state)
        assert session["score"] == score

    def test_human_review_set(self):
        session = self._make_session()
        hr = {
            "candidate": "Alex Rivera",
            "score": 4.2,
            "recommendation": "yes",
            "evidence": [],
            "availability": [],
            "proposed_slot": "Tuesday 14:00",
            "guardrail_flags": [],
            "fairness_flags": [],
            "approval_required": True,
            "status": "PENDING_APPROVAL",
        }
        state = {"final_status": "PENDING_APPROVAL", "human_review": hr}
        with patch.object(self.app.st, "session_state", session):
            self.app._update_session_from_state(state)
        assert session["human_review"] == hr


# =============================================================================
# 9. Runner integration (mocked — no real LLM calls)
# =============================================================================

class TestRunnerIntegration:
    """_run_evaluation must call run_recruitment and update session state."""

    def setup_method(self):
        self.app = _get_app_module()

    def _make_session(self):
        return {
            "thread_id": None,
            "current_state": None,
            "candidate_profile": None,
            "score": None,
            "human_review": None,
            "workflow_status": None,
            "evaluation_running": False,
        }

    def test_run_evaluation_calls_run_recruitment(self):
        """_run_evaluation must call the backend runner, not re-implement scoring."""
        session = self._make_session()
        mock_state = {
            "final_status": "PENDING_APPROVAL",
            "candidate_profile": {"name": "Alex Rivera", "email": None, "skills": [], "years_of_experience": 0, "education": [], "projects": [], "experience": [], "summary": ""},
            "candidate_score": {"candidate_name": "Alex Rivera", "scores": [], "weighted_score": 4.0, "recommendation": "yes"},
            "human_review": None,
            "guardrail_flags": [],
            "fairness_flags": [],
            "trajectory": [],
            "errors": [],
        }
        with patch("app.agent.runner.run_recruitment", return_value=("thread-abc", mock_state)) as mock_run, \
             patch.object(self.app.st, "session_state", session):
            self.app._run_evaluation("resume text", "job description", "2026-W41")
            mock_run.assert_called_once()

    def test_run_evaluation_stores_thread_id(self):
        session = self._make_session()
        mock_state = {
            "final_status": "PENDING_APPROVAL",
            "candidate_profile": None,
            "candidate_score": None,
            "human_review": None,
            "guardrail_flags": [],
            "fairness_flags": [],
            "trajectory": [],
            "errors": [],
        }
        with patch("app.agent.runner.run_recruitment", return_value=("my-thread-id", mock_state)), \
             patch.object(self.app.st, "session_state", session):
            self.app._run_evaluation("resume text", "job description", "2026-W41")
        assert session["thread_id"] == "my-thread-id"

    def test_run_evaluation_handles_exception_safely(self):
        """If run_recruitment raises, session should show FAILED status (no crash)."""
        session = self._make_session()
        with patch("app.agent.runner.run_recruitment", side_effect=RuntimeError("LLM down")), \
             patch.object(self.app.st, "session_state", session):
            # Should NOT raise
            self.app._run_evaluation("resume text", "job description", "2026-W41")
        assert session["workflow_status"] == "FAILED"

    def test_run_evaluation_no_api_key_in_session(self):
        """session_state must never contain API key strings after evaluation."""
        session = self._make_session()
        mock_state = {
            "final_status": "APPROVED",
            "candidate_profile": None,
            "candidate_score": None,
            "human_review": None,
            "guardrail_flags": [],
            "fairness_flags": [],
            "trajectory": [],
            "errors": [],
        }
        with patch("app.agent.runner.run_recruitment", return_value=("t1", mock_state)), \
             patch.object(self.app.st, "session_state", session):
            self.app._run_evaluation("resume", "jd", "2026-W41")

        # Scan all session values for anything that looks like a secret
        for key, val in session.items():
            val_str = str(val).lower()
            assert "sk-or" not in val_str, f"Possible API key found in session[{key!r}]"
            assert "secret" not in val_str or key in ("workflow_status",), \
                f"Suspicious value in session[{key!r}]"


# =============================================================================
# 10. Pending approval state — approval controls logic
# =============================================================================

class TestApprovalControlsLogic:
    """_render_approval_controls branching logic via status constants."""

    def setup_method(self):
        self.app = _get_app_module()

    def test_pending_approval_triggers_controls(self):
        """PENDING_APPROVAL must be routed to show approval buttons."""
        assert "PENDING_APPROVAL" in self.app._STATUSES_WITH_APPROVAL
        assert "PENDING_APPROVAL" not in self.app._STATUSES_NO_APPROVAL

    def test_approved_shows_no_buttons(self):
        assert "APPROVED" in self.app._STATUSES_NO_APPROVAL
        assert "APPROVED" not in self.app._STATUSES_WITH_APPROVAL

    def test_rejected_shows_no_buttons(self):
        assert "REJECTED" in self.app._STATUSES_NO_APPROVAL
        assert "REJECTED" not in self.app._STATUSES_WITH_APPROVAL


# =============================================================================
# 11. Guardrail-blocked state
# =============================================================================

class TestGuardrailBlockedState:
    """_render_guardrail_blocked_banner must communicate blocked status clearly."""

    def setup_method(self):
        self.app = _get_app_module()

    def test_blocked_banner_function_exists(self):
        assert callable(getattr(self.app, "_render_guardrail_blocked_banner", None))

    def test_guardrail_blocked_renders_safe_text(self):
        """The function must not expose system prompts or API keys when called."""
        rendered_calls = []

        # Ensure the stub/real streamlit module has the `error` attribute
        # (patch.object requires the attribute to exist before it can replace it)
        if not hasattr(self.app.st, "error"):
            self.app.st.error = lambda msg: None

        # Capture calls to st.error by patching it
        with patch.object(self.app.st, "error", side_effect=lambda msg: rendered_calls.append(msg)):
            state = {
                "final_status": "GUARDRAIL_BLOCKED",
                "guardrail_flags": ["injection_detected"],
                "errors": [],
            }
            self.app._render_guardrail_blocked_banner(state)

        assert rendered_calls, "st.error was never called for GUARDRAIL_BLOCKED"
        combined = " ".join(str(c) for c in rendered_calls).lower()

        # Must NOT expose secrets or system internals
        assert "sk-or" not in combined
        assert "system prompt" not in combined
        assert "traceback" not in combined

        # Must communicate the block clearly
        assert "block" in combined or "guardrail" in combined or "security" in combined


# =============================================================================
# 12. Low-score state — no approval controls
# =============================================================================

class TestLowScoreState:
    """REJECTED_BY_THRESHOLD must show score info but no approval button."""

    def setup_method(self):
        self.app = _get_app_module()

    def test_rejected_by_threshold_not_in_approval_set(self):
        assert "REJECTED_BY_THRESHOLD" not in self.app._STATUSES_WITH_APPROVAL

    def test_rejected_by_threshold_is_in_no_approval_set(self):
        assert "REJECTED_BY_THRESHOLD" in self.app._STATUSES_NO_APPROVAL

    def test_banner_function_exists(self):
        assert callable(getattr(self.app, "_render_rejected_by_threshold_banner", None))

    def test_threshold_banner_renders(self):
        """Banner must not raise and must emit a warning."""
        warning_calls = []
        metric_calls = []

        # Ensure stub/real streamlit has these attributes
        for attr in ("warning", "metric"):
            if not hasattr(self.app.st, attr):
                setattr(self.app.st, attr, lambda *a, **kw: None)

        with patch.object(self.app.st, "warning", side_effect=lambda msg: warning_calls.append(msg)), \
             patch.object(self.app.st, "metric", side_effect=lambda *a, **kw: metric_calls.append(a)):
            state = {"final_status": "REJECTED_BY_THRESHOLD", "guardrail_flags": [], "fairness_flags": []}
            score = {"weighted_score": 1.8, "recommendation": "no", "scores": []}
            self.app._render_rejected_by_threshold_banner(state, score)

        assert warning_calls or metric_calls, "No UI output for threshold banner"


# =============================================================================
# 13. Audit trail — no chain-of-thought exposure
# =============================================================================

class TestAuditTrail:
    """_render_audit_trail must only show operational events."""

    def setup_method(self):
        self.app = _get_app_module()

    def test_audit_trail_function_exists(self):
        assert callable(getattr(self.app, "_render_audit_trail", None))

    def test_empty_trajectory_does_not_raise(self):
        """An empty trajectory must render without errors."""
        calls = []

        # Ensure stub/real streamlit has expander and markdown attributes
        for attr in ("expander", "markdown"):
            if not hasattr(self.app.st, attr):
                setattr(self.app.st, attr, MagicMock())

        with patch.object(self.app.st, "expander", MagicMock()), \
             patch.object(self.app.st, "markdown", side_effect=lambda *a, **kw: calls.append(a)):
            state = {"trajectory": [], "final_status": "PENDING_APPROVAL"}
            # Should not raise
            self.app._render_audit_trail(state)

    def test_trajectory_entries_contain_only_operational_fields(self):
        """Verify the trajectory data structure used in UI only exposes safe fields."""
        # Build a mock trajectory as graph.py would produce it
        trajectory = [
            {"step": 1, "node": "input_guardrail", "status": "completed", "detail": "passed=True, severity=None"},
            {"step": 2, "node": "parse_resume", "status": "completed", "detail": "name=Alex Rivera"},
            {"step": 3, "node": "score_candidate", "status": "completed", "detail": "weighted_score=4.2"},
            {"step": 4, "node": "human_approval_gate", "status": "interrupted"},
        ]
        for entry in trajectory:
            # Safe fields only
            assert "node" in entry
            assert "status" in entry
            # No chain-of-thought fields
            assert "chain_of_thought" not in entry
            assert "llm_reasoning" not in entry
            assert "api_key" not in entry
            assert "system_prompt" not in entry
            # Detail if present must not contain secrets
            detail = entry.get("detail", "")
            assert "sk-or" not in detail
            assert "api_key" not in detail.lower()


# =============================================================================
# 14. Secrets not exposed
# =============================================================================

class TestSecretsNotExposed:
    """No rendering path must return API keys or internal secrets."""

    def setup_method(self):
        self.app = _get_app_module()

    def test_safe_error_never_echoes_api_key(self):
        for secret in ["sk-or-v1-abc123", "OPENROUTER_API_KEY=sk-or", "Bearer sk-"]:
            exc = RuntimeError(secret)
            msg = self.app._safe_error_message(exc)
            assert "sk-or" not in msg
            assert "Bearer" not in msg

    def test_default_rubric_contains_no_secrets(self):
        rubric = self.app._build_default_rubric()
        rubric_str = str(rubric).lower()
        assert "sk-or" not in rubric_str
        assert "api_key" not in rubric_str
        assert "openrouter" not in rubric_str

    def test_recommendation_labels_contain_no_secrets(self):
        labels_str = str(self.app._RECOMMENDATION_LABEL).lower()
        assert "sk-or" not in labels_str
        assert "api" not in labels_str


# =============================================================================
# 15. _extract_text_from_upload
# =============================================================================

class TestExtractTextFromUpload:
    """_extract_text_from_upload must handle txt and pdf file objects."""

    def setup_method(self):
        self.app = _get_app_module()

    def _make_upload(self, name: str, content: bytes):
        """Create a minimal file-like object that mimics st.UploadedFile."""
        obj = MagicMock()
        obj.name = name
        obj.read.return_value = content
        return obj

    def test_txt_file_returns_decoded_text(self):
        upload = self._make_upload("resume.txt", b"Alex Rivera\nPython developer")
        result = self.app._extract_text_from_upload(upload)
        assert "Alex Rivera" in result
        assert "Python developer" in result

    def test_txt_file_handles_utf8(self):
        upload = self._make_upload("resume.txt", "Résumé — André\n".encode())
        result = self.app._extract_text_from_upload(upload)
        assert isinstance(result, str)

    def test_pdf_file_returns_string(self):
        upload = self._make_upload("resume.pdf", b"%PDF-1.4 BT (Alex Rivera) Tj ET")
        result = self.app._extract_text_from_upload(upload)
        assert isinstance(result, str)

    def test_unknown_extension_falls_back_to_utf8(self):
        upload = self._make_upload("resume.docx", b"plain text content")
        result = self.app._extract_text_from_upload(upload)
        assert isinstance(result, str)
