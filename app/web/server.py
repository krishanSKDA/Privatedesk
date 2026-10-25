import argparse
import re
import sys
import threading
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import uvicorn
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.accounting.mock_books import (
    MATCH,
    MOCK_LABEL,
    AccountingAccessRevoked,
    AccountingConnection,
    MockQuickBooks,
    compare_invoices,
)
from app.approvals.gate import PENDING, ApprovalError, ApprovalGate, draft_follow_up
from app.config import settings
from app.extraction.extractor import ExtractionError, InvoiceExtractor, discover_invoices
from app.generation.llm import LLMError, RateLimitExceeded
from app.generation.rag_chain import RagChain
from app.ingestion.embedder import EmbeddingError
from app.ingestion.loader import FileRejectedError, validate_file
from app.retrieval.retriever import RoleError
from app.retrieval.vector_store import VectorStoreError
from app.utils.logger import ActivityLog

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEMO_USERS = {
    "daniel": {"name": "Daniel Reyes", "team": "Finance", "role": "finance"},
    "priya": {"name": "Priya Nair", "team": "HR", "role": "hr"},
}
PAGES = {"finance": ["ask", "invoices", "approvals"], "hr": ["ask"]}
SUGGESTIONS = {
    "finance": [
        "What are the payment terms in the Bluestone freight agreement?",
        "Who has to approve an expense claim of USD 1,800?",
        "When did we pay Coastal Office Supplies, and how much?",
    ],
    "hr": [
        "How many unused leave days can be carried over?",
        "When is a medical certificate needed for sick leave?",
        "When is the warehouse moving to Dubai Investments Park?",
    ],
}
NOT_FOUND_TEXT = "I couldn't find this in the documents you can access, so I won't guess."
STATUS_LABELS = {
    "match": ("Matches the books", "ok"),
    "amount_mismatch": ("Amount differs", "alert"),
    "currency_mismatch": ("Currency differs", "alert"),
    "vendor_mismatch": ("Supplier differs", "alert"),
    "missing_from_books": ("Not in the books", "alert"),
    "needs_review": ("Needs a person", "alert"),
}
FIELD_LABELS = {
    "supplier": "Supplier",
    "invoice_date": "Date",
    "invoice_number": "Invoice number",
    "amount": "Amount",
    "currency": "Currency",
}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")
SERVICE_UNAVAILABLE = "The assistant could not reach the AI service. Try again in a minute."


class Question(BaseModel):
    question: str


class CheckRequest(BaseModel):
    include_holdout: bool = False


class FollowUpRequest(BaseModel):
    file: str


class AccountingRequest(BaseModel):
    enabled: bool


class Services:
    def __init__(self, chain=None, extractor=None, activity_log=None, connection=None, upload_dir=None):
        self._chain = chain
        self._extractor = extractor
        self._activity_log = activity_log
        self._connection = connection
        self.upload_dir = Path(upload_dir or settings.processed_dir / "uploads")
        self.checks = {}
        self._lock = threading.Lock()

    @property
    def activity_log(self):
        with self._lock:
            if self._activity_log is None:
                self._activity_log = ActivityLog()
        return self._activity_log

    @property
    def chain(self):
        log = self.activity_log
        with self._lock:
            if self._chain is None:
                self._chain = RagChain(activity_log=log)
        return self._chain

    @property
    def extractor(self):
        with self._lock:
            if self._extractor is None:
                self._extractor = InvoiceExtractor()
        return self._extractor

    @property
    def connection(self):
        return self._connection or AccountingConnection()

    def books(self):
        return MockQuickBooks(connection=self.connection)

    def gate(self):
        return ApprovalGate(activity_log=self.activity_log, connection=self.connection)

    def invoice_paths(self, include_holdout=False):
        uploads = sorted(path for path in self.upload_dir.glob("*") if path.is_file()) if self.upload_dir.exists() else []
        return discover_invoices(include_holdout) + uploads


def current_user(x_demo_user: str = Header(default="")):
    user = DEMO_USERS.get(x_demo_user)
    if user is None:
        raise HTTPException(401, "Choose a demo user to continue.")
    return {"id": x_demo_user, **user}


def finance_user(user=Depends(current_user)):
    if user["role"] != "finance":
        raise HTTPException(403, "This area is only available to Finance.")
    return user


def safe_upload_name(name):
    return UNSAFE_NAME.sub("_", Path(name or "upload").name).lstrip(".") or "upload"


def action_view(action):
    return {
        "id": action.id,
        "kind": action.kind,
        "title": action.title,
        "status": action.status,
        "warnings": list(action.warnings),
        "payload": action.payload,
        "created_at": action.created_at,
        "decided_by": action.decided_by,
        "decided_at": action.decided_at,
    }


def row_view(result, check):
    label, tone = STATUS_LABELS[check.status] if check else ("Not compared", "neutral")
    return {
        "file": result.file,
        "supplier": result.value("supplier"),
        "invoice_number": result.value("invoice_number"),
        "date": result.value("invoice_date"),
        "amount": result.value("amount"),
        "currency": result.value("currency"),
        "result": label,
        "tone": tone,
    }


def issue_view(check, result, drafted, waiting):
    label, tone = STATUS_LABELS[check.status]
    review = []
    if result is not None:
        for name in result.review_fields:
            field = result.fields[name]
            review.append({"field": FIELD_LABELS[name], "reasons": field.reasons, "suggestion": field.candidate})
    return {
        "file": check.file,
        "vendor": check.vendor or check.file,
        "label": label,
        "tone": tone,
        "message": check.message,
        "review": review,
        "drafted": check.file in drafted or check.file in waiting,
    }


def check_view(state, waiting=frozenset()):
    results, checks, drafted = state["results"], state["checks"], state["drafted"]
    issues = [check for check in checks.values() if check.status != MATCH]
    return {
        "compared": state["compared"],
        "errors": state["errors"],
        "rows": [row_view(result, checks.get(file)) for file, result in results.items()],
        "issues": [issue_view(check, results.get(check.file), drafted, waiting) for check in issues],
    }


def waiting_sources(services, role):
    return {action.source for action in services.gate().pending() if action.role == role and action.source}


def create_app(services=None):
    services = services or Services()
    app = FastAPI(title="PrivateDesk", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.services = services

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/api/bootstrap")
    def bootstrap():
        return {
            "provider": settings.chat_provider.title(),
            "users": [
                {"id": key, "name": user["name"], "team": user["team"], "pages": PAGES[user["role"]], "suggestions": SUGGESTIONS[user["role"]]}
                for key, user in DEMO_USERS.items()
            ],
        }

    @app.post("/api/ask")
    def ask(body: Question, user=Depends(current_user)):
        try:
            result = services.chain.answer(body.question, user["role"], user=user["name"])
        except RateLimitExceeded as error:
            raise HTTPException(429, str(error))
        except VectorStoreError:
            raise HTTPException(503, "The documents have not been prepared yet. Run python run_ingestion.py and try again.")
        except (LLMError, EmbeddingError, RoleError):
            raise HTTPException(502, SERVICE_UNAVAILABLE)
        except ValueError as error:
            raise HTTPException(400, str(error))
        if not result.found:
            return {"found": False, "answer": NOT_FOUND_TEXT, "sources": []}
        sources = [{"title": chunk.title, "page": chunk.page, "text": chunk.text} for chunk in result.citations]
        return {"found": True, "answer": result.answer, "sources": sources}

    @app.get("/api/invoices")
    def invoices(include_holdout: bool = False, user=Depends(finance_user)):
        return {"files": [path.name for path in services.invoice_paths(include_holdout)]}

    @app.post("/api/invoices/upload")
    async def upload(files: list[UploadFile] = File(...), user=Depends(finance_user)):
        services.upload_dir.mkdir(parents=True, exist_ok=True)
        saved, rejected = [], []
        for file in files:
            path = services.upload_dir / safe_upload_name(file.filename)
            path.write_bytes(await file.read(settings.max_file_bytes + 1))
            try:
                validate_file(path)
            except FileRejectedError as error:
                path.unlink(missing_ok=True)
                rejected.append(str(error))
                continue
            saved.append(path.name)
        return {"saved": saved, "rejected": rejected}

    @app.get("/api/invoices/check")
    def last_check(user=Depends(finance_user)):
        state = services.checks.get(user["id"])
        if state is None:
            raise HTTPException(404, "No invoices have been checked yet.")
        return check_view(state, waiting_sources(services, user["role"]))

    @app.post("/api/invoices/check")
    def check(body: CheckRequest, user=Depends(finance_user)):
        try:
            extractor = services.extractor
        except (LLMError, EmbeddingError):
            raise HTTPException(502, SERVICE_UNAVAILABLE)
        log = services.activity_log
        results, errors = {}, []
        for path in services.invoice_paths(body.include_holdout):
            try:
                result = extractor.extract(path)
                results[path.name] = result
                log.log_extraction(user["name"], user["role"], result)
            except FileRejectedError as error:
                errors.append(str(error))
            except (ExtractionError, LLMError, EmbeddingError):
                errors.append(f"{path.name} could not be read. Try checking it again.")
                log.record(user["name"], user["role"], "invoice_extraction", documents=(path.name,), outcome="error")
        try:
            checks = compare_invoices(list(results.values()), services.books())
            log.log_comparison(user["name"], user["role"], checks)
            compared = True
        except AccountingAccessRevoked:
            checks, compared = [], False
        state = {"results": results, "checks": {item.file: item for item in checks}, "drafted": set(), "compared": compared, "errors": errors}
        services.checks[user["id"]] = state
        return check_view(state, waiting_sources(services, user["role"]))

    @app.post("/api/followups")
    def follow_up(body: FollowUpRequest, user=Depends(finance_user)):
        state = services.checks.get(user["id"])
        item = state["checks"].get(body.file) if state else None
        if item is None or item.status == MATCH:
            raise HTTPException(404, "There is nothing to follow up for this invoice.")
        gate = services.gate()
        already_waiting = any(action.source == body.file and action.role == user["role"] for action in gate.pending())
        if body.file in state["drafted"] or already_waiting:
            state["drafted"].add(body.file)
            raise HTTPException(409, "A follow-up for this invoice is already waiting in Approvals.")
        try:
            vendor_email = services.books().vendor_email(item.vendor) if item.vendor else None
            action = draft_follow_up(gate, item, vendor_email, user["name"], user["role"])
        except (AccountingAccessRevoked, ApprovalError) as error:
            raise HTTPException(409, str(error))
        if action is None:
            raise HTTPException(404, "There is no follow-up for this case yet.")
        state["drafted"].add(body.file)
        return action_view(action)

    @app.get("/api/approvals")
    def approvals(user=Depends(finance_user)):
        actions = [action for action in services.gate().list() if action.role == user["role"]]
        return {
            "pending": [action_view(action) for action in actions if action.status == PENDING],
            "history": [action_view(action) for action in actions if action.status != PENDING][:20],
        }

    @app.post("/api/approvals/{action_id}/{decision}")
    def decide(action_id: str, decision: str, user=Depends(finance_user)):
        if decision not in ("approve", "reject"):
            raise HTTPException(404, "Unknown decision.")
        gate = services.gate()
        try:
            action = gate.get(action_id)
        except ApprovalError:
            raise HTTPException(404, "This action no longer exists.")
        if action.role != user["role"]:
            raise HTTPException(404, "This action no longer exists.")
        decision_method = gate.approve if decision == "approve" else gate.reject
        try:
            return action_view(decision_method(action_id, user["name"], user["role"]))
        except ApprovalError as error:
            raise HTTPException(409, str(error))

    @app.get("/api/accounting")
    def accounting(user=Depends(finance_user)):
        state = services.connection.state()
        return {"enabled": state["enabled"], "changed_by": state["changed_by"], "changed_at": state["changed_at"], "label": MOCK_LABEL}

    @app.post("/api/accounting")
    def set_accounting(body: AccountingRequest, user=Depends(finance_user)):
        state = services.connection.set_enabled(body.enabled, user["name"])
        services.activity_log.log_access_change(user["name"], user["role"], enabled=body.enabled)
        return {"enabled": state["enabled"], "changed_by": state["changed_by"], "changed_at": state["changed_at"], "label": MOCK_LABEL}

    @app.get("/api/activity")
    def activity(user=Depends(finance_user)):
        return {
            "rows": [
                {"when": row["created_at"], "who": row["user"], "what": row["event"], "documents": row["documents"], "result": row["outcome"]}
                for row in services.activity_log.recent(100)
                if row["role"] == user["role"]
            ]
        }

    return app


def main():
    parser = argparse.ArgumentParser(description="Run the PrivateDesk web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    print(f"PrivateDesk is running at http://{args.host}:{args.port}")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
