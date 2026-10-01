"""check_availability tool — deterministic fake calendar for interview slot lookup.

For Phase 3 this tool does NOT connect to a real calendar API.
A deterministic fake calendar is used so tests are stable and reproducible.

The available slots are derived from the candidate name and ISO week string
using a hash, so:
  - The same (candidate, week) pair always returns the same slots.
  - Different candidates or weeks return (generally) different slots.
  - No real personal calendar data is used.

Phase 7/8 will replace the fake calendar with a real integration.
"""

from __future__ import annotations

import hashlib
import re

from langchain_core.tools import tool
from pydantic import BaseModel, Field

# ── Constants ─────────────────────────────────────────────────────────────────

_ISO_WEEK_RE = re.compile(r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$")

_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
_TIMES = ["09:00", "10:00", "11:00", "14:00", "15:00", "16:00"]

# Every possible (day, time) slot — 30 total.
_ALL_SLOTS: list[str] = [f"{day} {time}" for day in _DAYS for time in _TIMES]

# How many slots to return (3–5) — also derived from the hash for full determinism.
_MIN_SLOTS = 3
_MAX_SLOTS = 5


# ── Output model ──────────────────────────────────────────────────────────────


class AvailabilityResult(BaseModel):
    """Structured result returned by check_availability."""

    candidate: str = Field(min_length=1, description="Candidate name")
    week: str = Field(description="ISO week string, e.g. '2026-W41'")
    available_slots: list[str] = Field(description="Available interview time slots for the week")


# ── Service layer (no LLM needed — purely deterministic) ─────────────────────


def check_availability_service(candidate: str, week: str) -> AvailabilityResult:
    """Return deterministic interview slots for a candidate in a given ISO week.

    Args:
        candidate: Candidate name (non-empty).
        week:      ISO week string in the form ``YYYY-Www``, e.g. ``2026-W41``.

    Returns:
        ``AvailabilityResult`` with a stable list of available slots.

    Raises:
        ValueError: if candidate is empty or week format is invalid.
    """
    candidate = candidate.strip()
    if not candidate:
        raise ValueError("Candidate name must not be empty.")

    week = week.strip()
    if not _ISO_WEEK_RE.match(week):
        raise ValueError(
            f"Invalid week format '{week}'. Expected 'YYYY-Www' (e.g. '2026-W41')."
        )

    slots = _deterministic_slots(candidate, week)
    return AvailabilityResult(candidate=candidate, week=week, available_slots=slots)


def _deterministic_slots(candidate: str, week: str) -> list[str]:
    """Derive a stable set of available slots from the candidate name and week.

    Uses SHA-256 so the output is reproducible across platforms and Python
    versions, unlike ``hash()`` which is randomised per process.
    """
    seed = f"{candidate.lower()}::{week}".encode()
    digest = hashlib.sha256(seed).digest()

    # Use successive bytes of the digest to pick slots without replacement.
    n_slots = _MIN_SLOTS + (digest[0] % (_MAX_SLOTS - _MIN_SLOTS + 1))
    available: list[str] = []
    seen: set[int] = set()
    byte_idx = 1  # start at index 1; index 0 was used for n_slots

    while len(available) < n_slots:
        idx = digest[byte_idx % len(digest)] % len(_ALL_SLOTS)
        if idx not in seen:
            seen.add(idx)
            available.append(_ALL_SLOTS[idx])
        byte_idx += 1
        if byte_idx > 255:
            # Extremely unlikely with 30 slots and max 5 picks, but be safe.
            break

    return available


# ── @tool wrapper (used by LangGraph) ────────────────────────────────────────


@tool
def check_availability(candidate: str, week: str) -> dict:
    """Return available interview slots for a candidate in an ISO week.

    This tool uses a deterministic fake calendar — it does NOT connect to any
    real calendar service or use real personal schedule data.

    Args:
        candidate: The candidate's name.
        week:      ISO week string, e.g. ``"2026-W41"``.

    Returns:
        A dict with keys ``candidate``, ``week``, and ``available_slots``
        (list of strings like ``"Monday 10:00"``).

    Raises:
        ValueError: if ``candidate`` is empty or ``week`` is not valid ISO week.
    """
    return check_availability_service(candidate, week).model_dump()
