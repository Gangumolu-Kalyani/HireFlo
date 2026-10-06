"""Persistent audit storage for HireFlo recruitment workflows.

Only operational workflow information is persisted here. Raw resumes,
job descriptions, prompts, model responses, secrets, and private LLM
reasoning must never be written to the audit store.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg

from app.agent.state import TrajectoryEntry
from app.config import Settings, get_settings
from app.observability.logging_config import get_logger

logger = get_logger(__name__)


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS recruitment_audit_logs (
    id BIGSERIAL PRIMARY KEY,
    correlation_id TEXT NOT NULL,
    workflow_status TEXT NOT NULL,
    step INTEGER NOT NULL,
    node TEXT NOT NULL,
    event_status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (correlation_id, step, node, event_status)
)
"""


_INSERT_SQL = """
INSERT INTO recruitment_audit_logs (
    correlation_id,
    workflow_status,
    step,
    node,
    event_status,
    detail,
    created_at
)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (correlation_id, step, node, event_status)
DO NOTHING
"""


class AuditStore:
    """Persist safe recruitment workflow audit events to PostgreSQL."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def _database_url(self) -> str | None:
        """Return the configured database URL without logging it."""

        database_url = self._settings.database_url

        if database_url is None:
            return None

        value = database_url.get_secret_value().strip()
        return value or None

    def is_enabled(self) -> bool:
        """Return whether persistent audit storage is configured."""

        return self._database_url() is not None

    def ensure_schema(self) -> bool:
        """Create the audit table when persistent storage is configured."""

        database_url = self._database_url()

        if database_url is None:
            logger.info(
                "Persistent audit storage disabled | "
                "reason=database_url_not_configured"
            )
            return False

        try:
            with psycopg.connect(database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(_CREATE_TABLE_SQL)

            logger.info("Persistent audit schema ready")
            return True

        except psycopg.Error as exc:
            logger.error(
                "Persistent audit schema setup failed | error_type=%s",
                type(exc).__name__,
            )
            return False

    def persist_trajectory(
        self,
        correlation_id: str,
        workflow_status: str,
        trajectory: list[TrajectoryEntry] | list[dict[str, Any]],
    ) -> int:
        """Persist workflow trajectory entries.

        Duplicate workflow events are ignored by PostgreSQL using the
        correlation ID and trajectory event identity.

        The return value is the number of trajectory entries submitted for
        persistence. Database failures are logged but do not crash the
        recruitment workflow.
        """

        database_url = self._database_url()

        if database_url is None:
            logger.info(
                "Persistent audit write skipped | "
                "correlation_id=%s | reason=database_url_not_configured",
                correlation_id,
            )
            return 0

        if not trajectory:
            return 0

        created_at = datetime.now(timezone.utc)

        rows = [
            (
                correlation_id,
                workflow_status,
                int(entry.get("step", 0)),
                str(entry.get("node", "")),
                str(entry.get("status", "")),
                str(entry.get("detail", "")),
                created_at,
            )
            for entry in trajectory
        ]

        try:
            with psycopg.connect(database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.executemany(_INSERT_SQL, rows)

            logger.info(
                "Persistent audit records submitted | "
                "correlation_id=%s | count=%d",
                correlation_id,
                len(rows),
            )

            return len(rows)

        except psycopg.Error as exc:
            logger.error(
                "Persistent audit write failed | "
                "correlation_id=%s | error_type=%s",
                correlation_id,
                type(exc).__name__,
            )
            return 0
