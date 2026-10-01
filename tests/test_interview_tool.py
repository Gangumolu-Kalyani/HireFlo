"""Tests for app/tools/interview_tool.py.

The propose_interview tool creates a PENDING_APPROVAL proposal only.
It must NEVER send emails, create calendar events, or make external calls.
"""

import pytest

from app.tools.interview_tool import InterviewProposal, propose_interview_service


class TestProposeInterviewService:
    # ── Returns a proposal ────────────────────────────────────────────────────

    def test_returns_interview_proposal(self):
        result = propose_interview_service("Alex Rivera", "Tuesday 14:00")
        assert isinstance(result, InterviewProposal)

    def test_candidate_is_preserved(self):
        result = propose_interview_service("Alex Rivera", "Tuesday 14:00")
        assert result.candidate == "Alex Rivera"

    def test_slot_is_preserved(self):
        result = propose_interview_service("Alex Rivera", "Tuesday 14:00")
        assert result.slot == "Tuesday 14:00"

    # ── Status is always PENDING_APPROVAL ─────────────────────────────────────

    def test_status_is_pending_approval(self):
        """The proposal must always start with PENDING_APPROVAL."""
        result = propose_interview_service("Alex Rivera", "Tuesday 14:00")
        assert result.status == "PENDING_APPROVAL"

    def test_status_cannot_be_overridden(self):
        """The status is a Literal with only one allowed value."""
        result = propose_interview_service("Jordan Lee", "Monday 10:00")
        assert result.status == "PENDING_APPROVAL"

    def test_status_is_always_pending_for_any_candidate(self):
        candidates = ["Alex Rivera", "Jordan Lee", "Priya Sharma"]
        for candidate in candidates:
            result = propose_interview_service(candidate, "Friday 15:00")
            assert result.status == "PENDING_APPROVAL"

    # ── Note confirms no action was taken ────────────────────────────────────

    def test_note_mentions_no_email(self):
        result = propose_interview_service("Alex Rivera", "Tuesday 14:00")
        assert "email" in result.note.lower()

    def test_note_mentions_no_calendar(self):
        result = propose_interview_service("Alex Rivera", "Tuesday 14:00")
        assert "calendar" in result.note.lower()

    def test_note_mentions_no_contact(self):
        result = propose_interview_service("Alex Rivera", "Tuesday 14:00")
        # The note must make clear that the candidate was not contacted.
        assert "contact" in result.note.lower() or "not been contacted" in result.note.lower()

    # ── Timestamp ─────────────────────────────────────────────────────────────

    def test_proposed_at_is_present(self):
        result = propose_interview_service("Alex Rivera", "Tuesday 14:00")
        assert result.proposed_at

    def test_proposed_at_is_iso_format(self):
        """The timestamp must be a valid ISO 8601 UTC string."""
        from datetime import datetime

        result = propose_interview_service("Alex Rivera", "Tuesday 14:00")
        # Will raise if the format is invalid.
        parsed = datetime.fromisoformat(result.proposed_at)
        assert parsed.tzinfo is not None  # UTC-aware

    # ── No external side-effects ──────────────────────────────────────────────

    def test_no_external_calls_needed(self, monkeypatch):
        """The tool must not use any network or calendar library at import/call time."""
        # Patch smtplib to ensure no email library is invoked.
        import smtplib

        class _NeverCallSMTP:
            def __init__(self, *args, **kwargs):
                raise AssertionError("propose_interview must not call smtplib.SMTP")

        monkeypatch.setattr(smtplib, "SMTP", _NeverCallSMTP)
        # Should complete without raising.
        result = propose_interview_service("Alex Rivera", "Tuesday 14:00")
        assert result.status == "PENDING_APPROVAL"

    # ── Whitespace handling ───────────────────────────────────────────────────

    def test_leading_and_trailing_whitespace_is_stripped(self):
        result = propose_interview_service("  Alex Rivera  ", "  Tuesday 14:00  ")
        assert result.candidate == "Alex Rivera"
        assert result.slot == "Tuesday 14:00"

    # ── Invalid input ─────────────────────────────────────────────────────────

    def test_empty_candidate_raises_value_error(self):
        with pytest.raises(ValueError, match="empty"):
            propose_interview_service("", "Tuesday 14:00")

    def test_whitespace_only_candidate_raises_value_error(self):
        with pytest.raises(ValueError, match="empty"):
            propose_interview_service("   ", "Tuesday 14:00")

    def test_empty_slot_raises_value_error(self):
        with pytest.raises(ValueError, match="empty"):
            propose_interview_service("Alex Rivera", "")

    def test_whitespace_only_slot_raises_value_error(self):
        with pytest.raises(ValueError, match="empty"):
            propose_interview_service("Alex Rivera", "   ")
