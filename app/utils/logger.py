import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
)
REDACTED = "[redacted]"

ACTIVITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    user TEXT NOT NULL,
    role TEXT NOT NULL,
    event TEXT NOT NULL,
    question TEXT,
    documents TEXT,
    outcome TEXT,
    details TEXT
)
"""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact(text):
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


class ActivityLog:
    def __init__(self, path=None):
        self.path = Path(path or settings.activity_db_path)
        with closing(connect(self.path)) as connection, connection:
            connection.execute(ACTIVITY_SCHEMA)

    def record(self, user, role, event, question=None, documents=(), outcome=None, details=None):
        row = (
            utc_now(),
            user,
            role,
            event,
            redact(question) if question else None,
            ",".join(documents),
            outcome,
            redact(json.dumps(details, sort_keys=True)) if details else None,
        )
        with closing(connect(self.path)) as connection, connection:
            connection.execute(
                "INSERT INTO activity (created_at, user, role, event, question, documents, outcome, details) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )

    def log_question(self, user, role, answer):
        retrieved = list(dict.fromkeys(chunk.document for chunk in answer.retrieved))
        self.record(
            user, role, "question", question=answer.question, documents=answer.cited_documents,
            outcome=answer.reason, details={"retrieved_documents": retrieved},
        )

    def log_extraction(self, user, role, result):
        self.record(
            user, role, "invoice_extraction", documents=(result.file,),
            outcome="needs_review" if result.needs_review else "ok",
            details={"review_fields": result.review_fields, "scanned": result.scanned},
        )

    def log_comparison(self, user, role, discrepancies):
        self.record(
            user, role, "books_comparison", documents=tuple(item.file for item in discrepancies),
            outcome=f"{sum(item.is_issue for item in discrepancies)} issues",
            details={item.file: item.status for item in discrepancies},
        )

    def log_action(self, user, role, action, event):
        self.record(
            user, role, event, documents=(action.source,) if action.source else (), outcome=action.status,
            details={"action_id": action.id, "kind": action.kind, "warnings": len(action.warnings)},
        )

    def log_access_change(self, user, role, enabled):
        self.record(user, role, "accounting_access", outcome="restored" if enabled else "revoked")

    def recent(self, limit=50):
        with closing(connect(self.path)) as connection:
            rows = connection.execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]
