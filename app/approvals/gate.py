import json
import re
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from app.accounting.mock_books import (
    AMOUNT_MISMATCH,
    MISSING_FROM_BOOKS,
    NEEDS_REVIEW,
    AccountingAccessRevoked,
    AccountingConnection,
    MockQuickBooks,
)
from app.config import settings
from app.utils.logger import connect, utc_now

SEND_EMAIL = "send_email"
ACCOUNTING_CHANGE = "accounting_change"
PENDING = "pending"
EXECUTED = "executed"
REJECTED = "rejected"
NON_HUMAN_APPROVERS = {"", "assistant", "system", "ai", "model", "privatedesk"}
ACCOUNTING_ROLES = {"finance"}
EMAIL_PATTERN = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}")

ACTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    payload TEXT NOT NULL,
    warnings TEXT NOT NULL,
    created_by TEXT NOT NULL,
    role TEXT NOT NULL,
    source TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    decision_note TEXT
)
"""


class ApprovalError(RuntimeError):
    pass


@dataclass(frozen=True)
class Action:
    id: str
    kind: str
    title: str
    payload: dict
    warnings: tuple[str, ...]
    created_by: str
    role: str
    source: str | None
    status: str
    created_at: str
    decided_at: str | None = None
    decided_by: str | None = None
    decision_note: str | None = None

    @classmethod
    def from_row(cls, row):
        values = dict(row)
        values["payload"] = json.loads(values["payload"])
        values["warnings"] = tuple(json.loads(values["warnings"]))
        return cls(**values)


def known_vendor_emails():
    try:
        return {
            vendor["PrimaryEmailAddr"]["Address"].lower()
            for vendor in MockQuickBooks().vendors()
            if vendor.get("PrimaryEmailAddr", {}).get("Address")
        }
    except AccountingAccessRevoked:
        return set()


class ApprovalGate:
    def __init__(self, db_path=None, outbox_path=None, activity_log=None, connection=None, vendor_emails=None):
        self.db_path = Path(db_path or settings.activity_db_path)
        self.outbox_path = Path(outbox_path or settings.outbox_path)
        self.activity_log = activity_log
        self.connection = connection or AccountingConnection()
        self.vendor_emails = {email.lower() for email in (known_vendor_emails() if vendor_emails is None else vendor_emails)}
        with closing(connect(self.db_path)) as db, db:
            db.execute(ACTIONS_SCHEMA)

    def recipient_warnings(self, to):
        address = to.strip().lower()
        domain = address.rsplit("@", 1)[1]
        if domain == settings.internal_email_domain or address in self.vendor_emails:
            return ()
        if domain in {email.rsplit("@", 1)[1] for email in self.vendor_emails}:
            return ("This address is not in the vendor file, although its domain is known.",)
        return ("This recipient is not in the vendor file or the company domain. Check the address before approving.",)

    def draft_email(self, to, subject, body, created_by, role, source=None):
        if not EMAIL_PATTERN.fullmatch(to.strip().lower()):
            raise ApprovalError(f"'{to}' is not a valid email address.")
        payload = {"to": to.strip(), "subject": subject, "body": body}
        return self._create(SEND_EMAIL, f"Email to {to.strip()}: {subject}", payload, self.recipient_warnings(to), created_by, role, source)

    def draft_accounting_change(self, change, bill_number, details, created_by, role, source=None):
        if role not in ACCOUNTING_ROLES:
            raise ApprovalError("Only Finance users can draft accounting changes.")
        payload = {"change": change, "bill_number": bill_number, **details}
        title = f"Accounting change: {change}" + (f" ({bill_number})" if bill_number else "")
        return self._create(ACCOUNTING_CHANGE, title, payload, (), created_by, role, source)

    def _create(self, kind, title, payload, warnings, created_by, role, source):
        action = Action(
            id=uuid.uuid4().hex[:12], kind=kind, title=title, payload=payload, warnings=tuple(warnings),
            created_by=created_by, role=role, source=source, status=PENDING, created_at=utc_now(),
        )
        with closing(connect(self.db_path)) as db, db:
            db.execute(
                "INSERT INTO actions (id, kind, title, payload, warnings, created_by, role, source, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (action.id, kind, title, json.dumps(payload), json.dumps(list(warnings)), created_by, role, source, PENDING, action.created_at),
            )
        self._log(created_by, role, action, "action_drafted")
        return action

    def get(self, action_id):
        with closing(connect(self.db_path)) as db:
            row = db.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        if row is None:
            raise ApprovalError(f"No action with id {action_id}.")
        return Action.from_row(row)

    def list(self, status=None):
        query, params = "SELECT * FROM actions ORDER BY created_at DESC, rowid DESC", ()
        if status:
            query, params = "SELECT * FROM actions WHERE status = ? ORDER BY created_at DESC, rowid DESC", (status,)
        with closing(connect(self.db_path)) as db:
            return [Action.from_row(row) for row in db.execute(query, params).fetchall()]

    def pending(self):
        return self.list(PENDING)

    def approve(self, action_id, approver, role, note=None):
        approver = self._require_human(approver)
        action = self._require_pending(action_id)
        if action.kind == ACCOUNTING_CHANGE:
            if role not in ACCOUNTING_ROLES:
                raise ApprovalError("Only Finance users can approve accounting changes.")
            if not self.connection.enabled:
                self._log(approver, role, action, "action_blocked")
                raise ApprovalError("Accounting access is revoked, so accounting changes cannot be approved.")
        self._decide(action_id, EXECUTED, approver, note)
        executed = self.get(action_id)
        self._simulate(executed)
        self._log(approver, role, executed, "action_approved")
        return executed

    def reject(self, action_id, approver, role, note=None):
        approver = self._require_human(approver)
        self._require_pending(action_id)
        self._decide(action_id, REJECTED, approver, note)
        rejected = self.get(action_id)
        self._log(approver, role, rejected, "action_rejected")
        return rejected

    @staticmethod
    def _require_human(approver):
        approver = (approver or "").strip()
        if approver.lower() in NON_HUMAN_APPROVERS:
            raise ApprovalError("Actions must be approved or rejected by a named person.")
        return approver

    def _require_pending(self, action_id):
        action = self.get(action_id)
        if action.status != PENDING:
            raise ApprovalError(f"Action {action_id} is already {action.status}.")
        return action

    def _decide(self, action_id, status, approver, note):
        with closing(connect(self.db_path)) as db, db:
            cursor = db.execute(
                "UPDATE actions SET status = ?, decided_at = ?, decided_by = ?, decision_note = ? WHERE id = ? AND status = ?",
                (status, utc_now(), approver, note, action_id, PENDING),
            )
        if cursor.rowcount != 1:
            raise ApprovalError(f"Action {action_id} was already decided.")

    def _simulate(self, action):
        self.outbox_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "simulated": True,
            "action_id": action.id,
            "kind": action.kind,
            "approved_by": action.decided_by,
            "executed_at": action.decided_at,
            "payload": action.payload,
        }
        with self.outbox_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def _log(self, user, role, action, event):
        if self.activity_log:
            self.activity_log.log_action(user, role, action, event)


def draft_follow_up(gate, discrepancy, vendor_email, created_by, role):
    number = discrepancy.invoice_number
    if discrepancy.status == AMOUNT_MISMATCH and vendor_email:
        body = (
            f"Hello,\n\nWe received invoice {number} for {discrepancy.currency} {discrepancy.invoice_amount}. "
            f"Our records show {discrepancy.currency} {discrepancy.books_amount} for this invoice. "
            "Could you please confirm the correct amount?\n\nKind regards,\nAccounts Payable"
        )
        return gate.draft_email(vendor_email, f"Query on invoice {number}", body, created_by, role, source=discrepancy.file)
    if discrepancy.status == MISSING_FROM_BOOKS:
        details = {"vendor": discrepancy.vendor, "amount": discrepancy.invoice_amount, "currency": discrepancy.currency}
        return gate.draft_accounting_change("create bill", number, details, created_by, role, source=discrepancy.file)
    if discrepancy.status == NEEDS_REVIEW and vendor_email:
        body = (
            f"Hello,\n\nWe received an invoice from you for {discrepancy.invoice_amount or 'an unconfirmed amount'} "
            "without an invoice number. Could you please send a corrected invoice showing the invoice number, "
            "the invoice date and the currency?\n\nKind regards,\nAccounts Payable"
        )
        return gate.draft_email(vendor_email, "Missing invoice number", body, created_by, role, source=discrepancy.file)
    return None
