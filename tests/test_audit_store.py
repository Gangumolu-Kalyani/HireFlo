"""Tests for persistent recruitment audit storage.

These tests never connect to a real PostgreSQL database.
Database connections are mocked so the audit layer can be tested safely
and deterministically.
"""

from unittest.mock import MagicMock, patch

import psycopg

from app.config import Settings
from app.observability.audit_store import _INSERT_SQL, AuditStore


def _settings(database_url: str | None = None) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
    )


def test_audit_store_disabled_without_database_url():
    store = AuditStore(settings=_settings())

    assert store.is_enabled() is False
    assert store.ensure_schema() is False

    written = store.persist_trajectory(
        correlation_id="test-thread",
        workflow_status="APPROVED",
        trajectory=[
            {
                "step": 1,
                "node": "parse_resume",
                "status": "completed",
                "detail": "resume parsed",
            }
        ],
    )

    assert written == 0


def test_audit_store_enabled_with_database_url():
    store = AuditStore(
        settings=_settings(
            "postgresql://user:password@localhost:5432/hireflo"
        )
    )

    assert store.is_enabled() is True


@patch("app.observability.audit_store.psycopg.connect")
def test_ensure_schema_creates_table(mock_connect):
    connection = MagicMock()
    cursor = MagicMock()

    mock_connect.return_value.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor

    store = AuditStore(
        settings=_settings(
            "postgresql://user:password@localhost:5432/hireflo"
        )
    )

    result = store.ensure_schema()

    assert result is True
    cursor.execute.assert_called_once()


@patch("app.observability.audit_store.psycopg.connect")
def test_persist_trajectory_writes_safe_fields(mock_connect):
    connection = MagicMock()
    cursor = MagicMock()

    mock_connect.return_value.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor

    store = AuditStore(
        settings=_settings(
            "postgresql://user:password@localhost:5432/hireflo"
        )
    )

    trajectory = [
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
    ]

    written = store.persist_trajectory(
        correlation_id="correlation-123",
        workflow_status="SCORED",
        trajectory=trajectory,
    )

    assert written == 2

    cursor.executemany.assert_called_once()

    _, rows = cursor.executemany.call_args.args

    assert len(rows) == 2

    assert rows[0][0] == "correlation-123"
    assert rows[0][1] == "SCORED"
    assert rows[0][2] == 1
    assert rows[0][3] == "parse_resume"
    assert rows[0][4] == "completed"
    assert rows[0][5] == "resume parsed"


@patch("app.observability.audit_store.psycopg.connect")
def test_empty_trajectory_does_not_connect(mock_connect):
    store = AuditStore(
        settings=_settings(
            "postgresql://user:password@localhost:5432/hireflo"
        )
    )

    written = store.persist_trajectory(
        correlation_id="correlation-123",
        workflow_status="APPROVED",
        trajectory=[],
    )

    assert written == 0
    mock_connect.assert_not_called()


@patch("app.observability.audit_store.psycopg.connect")
def test_database_failure_does_not_crash_workflow(mock_connect):
    mock_connect.side_effect = psycopg.OperationalError(
        "database unavailable"
    )

    store = AuditStore(
        settings=_settings(
            "postgresql://user:password@localhost:5432/hireflo"
        )
    )

    written = store.persist_trajectory(
        correlation_id="correlation-123",
        workflow_status="FAILED",
        trajectory=[
            {
                "step": 3,
                "node": "score_candidate",
                "status": "error",
                "detail": "scoring failed",
            }
        ],
    )

    assert written == 0


@patch("app.observability.audit_store.psycopg.connect")
def test_schema_failure_does_not_raise(mock_connect):
    mock_connect.side_effect = psycopg.OperationalError(
        "database unavailable"
    )

    store = AuditStore(
        settings=_settings(
            "postgresql://user:password@localhost:5432/hireflo"
        )
    )

    assert store.ensure_schema() is False


def test_insert_uses_database_duplicate_protection():
    normalized_sql = " ".join(_INSERT_SQL.split()).upper()

    assert "ON CONFLICT" in normalized_sql
    assert "CORRELATION_ID" in normalized_sql
    assert "STEP" in normalized_sql
    assert "NODE" in normalized_sql
    assert "EVENT_STATUS" in normalized_sql
    assert "DO NOTHING" in normalized_sql
