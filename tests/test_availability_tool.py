"""Tests for app/tools/availability_tool.py.

The fake calendar is deterministic: same input → same output, always.
No real calendar API or network calls are made.
"""

import pytest

from app.tools.availability_tool import (
    _ALL_SLOTS,
    _MAX_SLOTS,
    _MIN_SLOTS,
    AvailabilityResult,
    check_availability_service,
)


class TestCheckAvailabilityService:
    # ── Happy path ────────────────────────────────────────────────────────────

    def test_returns_availability_result(self):
        result = check_availability_service("Alex Rivera", "2026-W41")
        assert isinstance(result, AvailabilityResult)

    def test_candidate_and_week_are_preserved(self):
        result = check_availability_service("Alex Rivera", "2026-W41")
        assert result.candidate == "Alex Rivera"
        assert result.week == "2026-W41"

    def test_slots_are_non_empty(self):
        result = check_availability_service("Alex Rivera", "2026-W41")
        assert len(result.available_slots) >= _MIN_SLOTS

    def test_slot_count_within_expected_range(self):
        result = check_availability_service("Alex Rivera", "2026-W41")
        assert _MIN_SLOTS <= len(result.available_slots) <= _MAX_SLOTS

    def test_all_slots_are_known_format(self):
        """Every slot must be in the set of valid (Day HH:MM) strings."""
        result = check_availability_service("Alex Rivera", "2026-W41")
        for slot in result.available_slots:
            assert slot in _ALL_SLOTS, f"Unknown slot: {slot}"

    # ── Determinism ───────────────────────────────────────────────────────────

    def test_same_input_returns_same_slots(self):
        """The fake calendar is deterministic: calling twice gives the same result."""
        r1 = check_availability_service("Alex Rivera", "2026-W41")
        r2 = check_availability_service("Alex Rivera", "2026-W41")
        assert r1.available_slots == r2.available_slots

    def test_different_week_gives_different_or_same_slots(self):
        """Different weeks can give different results (the hash changes)."""
        r_w41 = check_availability_service("Alex Rivera", "2026-W41")
        r_w42 = check_availability_service("Alex Rivera", "2026-W42")
        # They may coincidentally match, but both are valid — just verify no error.
        assert isinstance(r_w41.available_slots, list)
        assert isinstance(r_w42.available_slots, list)

    def test_different_candidates_in_same_week(self):
        """Two different candidates in the same week both get valid (possibly different) slots."""
        r_alex = check_availability_service("Alex Rivera", "2026-W41")
        r_jordan = check_availability_service("Jordan Lee", "2026-W41")
        assert _MIN_SLOTS <= len(r_alex.available_slots) <= _MAX_SLOTS
        assert _MIN_SLOTS <= len(r_jordan.available_slots) <= _MAX_SLOTS

    def test_slots_are_unique_within_a_result(self):
        """No duplicate slots in a single result."""
        result = check_availability_service("Alex Rivera", "2026-W41")
        assert len(result.available_slots) == len(set(result.available_slots))

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_week_1_is_valid(self):
        result = check_availability_service("Alex Rivera", "2026-W01")
        assert isinstance(result, AvailabilityResult)

    def test_week_53_is_valid(self):
        result = check_availability_service("Alex Rivera", "2026-W53")
        assert isinstance(result, AvailabilityResult)

    def test_leading_whitespace_is_stripped(self):
        """Leading/trailing whitespace in inputs is stripped before processing."""
        r1 = check_availability_service("Alex Rivera", "2026-W41")
        r2 = check_availability_service("  Alex Rivera  ", "  2026-W41  ")
        assert r1.available_slots == r2.available_slots

    # ── Invalid input ─────────────────────────────────────────────────────────

    def test_empty_candidate_raises_value_error(self):
        with pytest.raises(ValueError, match="empty"):
            check_availability_service("", "2026-W41")

    def test_whitespace_only_candidate_raises_value_error(self):
        with pytest.raises(ValueError, match="empty"):
            check_availability_service("   ", "2026-W41")

    @pytest.mark.parametrize(
        "bad_week",
        [
            "2026W41",        # missing hyphen
            "2026-41",        # missing W
            "W41",            # no year
            "2026-W0",        # week 0 (invalid)
            "2026-W54",       # week > 53
            "2026-W00",       # week 0 (zero-padded)
            "abcd-W41",       # non-numeric year
            "",               # empty
            "2026-w41",       # lowercase w
            "2026-W4",        # single digit week
        ],
    )
    def test_invalid_week_raises_value_error(self, bad_week):
        with pytest.raises(ValueError):
            check_availability_service("Alex Rivera", bad_week)
