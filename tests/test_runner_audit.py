"""Tests for runner integration with persistent audit storage."""

from unittest.mock import MagicMock, patch

from app.agent.runner import (
    _persist_audit,
    _trajectory_length_before_resume,
)


def test_persist_audit_writes_trajectory():
    state = {
        "final_status": "APPROVED",
        "trajectory": [
            {
                "step": 1,
                "node": "parse_resume",
                "status": "completed",
                "detail": "resume parsed",
            },
            {
                "step": 2,
                "node": "score_candidate",
                "status": "completed",
                "detail": "candidate scored",
            },
        ],
    }

    with patch("app.agent.runner.AuditStore") as mock_store_class:
        store = mock_store_class.return_value
        store.is_enabled.return_value = True
        store.ensure_schema.return_value = True

        _persist_audit(
            "thread-123",
            state,
        )

        store.persist_trajectory.assert_called_once_with(
            correlation_id="thread-123",
            workflow_status="APPROVED",
            trajectory=state["trajectory"],
        )


def test_persist_audit_skips_when_database_disabled():
    state = {
        "final_status": "APPROVED",
        "trajectory": [
            {
                "step": 1,
                "node": "parse_resume",
                "status": "completed",
            }
        ],
    }

    with patch("app.agent.runner.AuditStore") as mock_store_class:
        store = mock_store_class.return_value
        store.is_enabled.return_value = False

        _persist_audit(
            "thread-123",
            state,
        )

        store.ensure_schema.assert_not_called()
        store.persist_trajectory.assert_not_called()


def test_persist_audit_skips_when_schema_unavailable():
    state = {
        "final_status": "APPROVED",
        "trajectory": [
            {
                "step": 1,
                "node": "parse_resume",
                "status": "completed",
            }
        ],
    }

    with patch("app.agent.runner.AuditStore") as mock_store_class:
        store = mock_store_class.return_value
        store.is_enabled.return_value = True
        store.ensure_schema.return_value = False

        _persist_audit(
            "thread-123",
            state,
        )

        store.persist_trajectory.assert_not_called()


def test_persist_audit_only_writes_new_entries():
    trajectory = [
        {
            "step": 1,
            "node": "parse_resume",
            "status": "completed",
        },
        {
            "step": 2,
            "node": "score_candidate",
            "status": "completed",
        },
        {
            "step": 3,
            "node": "finalise_approval",
            "status": "completed",
        },
    ]

    state = {
        "final_status": "APPROVED",
        "trajectory": trajectory,
    }

    with patch("app.agent.runner.AuditStore") as mock_store_class:
        store = mock_store_class.return_value
        store.is_enabled.return_value = True
        store.ensure_schema.return_value = True

        _persist_audit(
            "thread-123",
            state,
            trajectory_start=2,
        )

        store.persist_trajectory.assert_called_once_with(
            correlation_id="thread-123",
            workflow_status="APPROVED",
            trajectory=[trajectory[2]],
        )


def test_persist_audit_does_nothing_when_no_new_entries():
    state = {
        "final_status": "APPROVED",
        "trajectory": [
            {
                "step": 1,
                "node": "parse_resume",
                "status": "completed",
            }
        ],
    }

    with patch("app.agent.runner.AuditStore") as mock_store_class:
        _persist_audit(
            "thread-123",
            state,
            trajectory_start=1,
        )

        mock_store_class.assert_not_called()


def test_persist_audit_handles_invalid_trajectory():
    state = {
        "final_status": "FAILED",
        "trajectory": "not-a-list",
    }

    with patch("app.agent.runner.AuditStore") as mock_store_class:
        _persist_audit(
            "thread-123",
            state,
        )

        mock_store_class.assert_not_called()


def test_persist_audit_failure_does_not_escape():
    state = {
        "final_status": "APPROVED",
        "trajectory": [
            {
                "step": 1,
                "node": "parse_resume",
                "status": "completed",
            }
        ],
    }

    with patch(
        "app.agent.runner.AuditStore",
        side_effect=RuntimeError("unexpected failure"),
    ):
        _persist_audit(
            "thread-123",
            state,
        )


def test_trajectory_length_before_resume():
    graph = MagicMock()

    snapshot = MagicMock()
    snapshot.values = {
        "trajectory": [
            {
                "step": 1,
                "node": "parse_resume",
                "status": "completed",
            },
            {
                "step": 2,
                "node": "score_candidate",
                "status": "completed",
            },
        ]
    }

    graph.get_state.return_value = snapshot

    length = _trajectory_length_before_resume(
        graph,
        "thread-123",
    )

    assert length == 2
    graph.get_state.assert_called_once_with(
        {"configurable": {"thread_id": "thread-123"}}
    )


def test_trajectory_length_returns_zero_on_failure():
    graph = MagicMock()
    graph.get_state.side_effect = RuntimeError("checkpoint unavailable")

    length = _trajectory_length_before_resume(
        graph,
        "thread-123",
    )

    assert length == 0
