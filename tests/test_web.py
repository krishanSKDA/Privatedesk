import json

import pytest
from fastapi.testclient import TestClient

from app.accounting.mock_books import AccountingConnection
from app.config import settings
from app.extraction.extractor import InvoiceExtractor
from app.generation.llm import RateLimiter
from app.generation.prompt import GroundedAnswer
from app.generation.rag_chain import RagChain
from app.utils.logger import ActivityLog
from app.web.server import STATIC_DIR, Services, create_app
from tests.test_rag import FakeChatModel, chunks, embedder, ingested, retriever, store
from tests.test_ui import FailingChatModel, RoutingChatModel

DANIEL = {"X-Demo-User": "daniel"}
PRIYA = {"X-Demo-User": "priya"}
ANSWER = "Net 30 days from the invoice date. <img src=x onerror=alert(1)>"


def make_client(tmp_path, monkeypatch, retriever, chat_model, rate_limit=1000):
    for name, value in {
        "activity_db_path": tmp_path / "activity.sqlite",
        "outbox_path": tmp_path / "outbox.jsonl",
        "accounting_state_path": tmp_path / "accounting_connection.json",
    }.items():
        monkeypatch.setitem(settings.__dict__, name, value)
    log = ActivityLog(tmp_path / "activity.sqlite")
    chain = RagChain(retriever=retriever, chat_model=chat_model, rate_limiter=RateLimiter(limit=rate_limit), min_relevance=0.0, activity_log=log)
    services = Services(
        chain=chain,
        extractor=InvoiceExtractor(chat_model=RoutingChatModel()),
        activity_log=log,
        connection=AccountingConnection(tmp_path / "accounting_connection.json"),
        upload_dir=tmp_path / "uploads",
    )
    return TestClient(create_app(services))


@pytest.fixture
def client(tmp_path, monkeypatch, retriever):
    reply = GroundedAnswer(found=True, answer=ANSWER, citations=["S1"])
    return make_client(tmp_path, monkeypatch, retriever, FakeChatModel(reply))


def test_page_is_served_with_a_strict_content_security_policy(client):
    response = client.get("/")
    assert response.status_code == 200 and "PrivateDesk" in response.text
    policy = response.headers["content-security-policy"]
    assert "default-src 'self'" in policy and "script-src 'self'" in policy and "frame-ancestors 'none'" in policy
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/docs").status_code == 404


def test_page_makes_no_external_requests():
    for name in ("index.html", "styles.css", "app.js"):
        source = (STATIC_DIR / name).read_text(encoding="utf-8")
        assert "http://" not in source and "https://" not in source, name


def test_frontend_never_inserts_html():
    source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in source and "outerHTML" not in source and "insertAdjacentHTML" not in source


def test_bootstrap_lists_demo_users_and_their_pages(client):
    data = client.get("/api/bootstrap").json()
    pages = {user["id"]: user["pages"] for user in data["users"]}
    assert pages == {"daniel": ["ask", "invoices", "approvals"], "priya": ["ask"]}


def test_unknown_user_is_refused(client):
    assert client.post("/api/ask", json={"question": "hi"}).status_code == 401
    assert client.post("/api/ask", json={"question": "hi"}, headers={"X-Demo-User": "admin"}).status_code == 401


def test_ask_returns_the_answer_text_unchanged_with_titled_sources(client, retriever):
    question = "What are the Bluestone payment terms?"
    data = client.post("/api/ask", json={"question": question}, headers=DANIEL).json()
    assert data["found"] is True and data["answer"] == ANSWER
    expected = retriever.retrieve(question, "finance")[0]
    assert data["sources"][0] == {"title": expected.title, "page": expected.page, "text": expected.text}


def test_role_comes_from_the_server_not_the_request(client):
    body = {"question": "parental leave secondary caregiver weeks", "role": "hr"}
    data = client.post("/api/ask", json=body, headers=DANIEL).json()
    assert not any("Leave Policy" in source["title"] for source in data["sources"])


def test_hr_cannot_reach_finance_areas(client):
    for method, path in (("get", "/api/invoices"), ("post", "/api/invoices/check"), ("get", "/api/approvals"), ("get", "/api/accounting"), ("get", "/api/activity")):
        response = getattr(client, method)(path, headers=PRIYA, **({"json": {}} if method == "post" else {}))
        assert response.status_code == 403, path
    assert client.post("/api/accounting", json={"enabled": False}, headers=PRIYA).status_code == 403


def test_invoice_check_follow_up_and_approval_flow(client):
    assert client.get("/api/invoices/check", headers=DANIEL).status_code == 404
    data = client.post("/api/invoices/check", json={}, headers=DANIEL).json()
    assert data["compared"] is True and data["errors"] == []
    results = {row["file"]: row["result"] for row in data["rows"]}
    assert results == {
        "apex_invoice.pdf": "Needs a person",
        "coastal_office_invoice.pdf": "Not in the books",
        "halcyon_invoice_sep2026.pdf": "Amount differs",
        "scan_20260923_cedar.pdf": "Matches the books",
    }
    apex = next(row for row in data["rows"] if row["file"] == "apex_invoice.pdf")
    assert apex["invoice_number"] is None and apex["currency"] is None
    action = client.post("/api/followups", json={"file": "halcyon_invoice_sep2026.pdf"}, headers=DANIEL).json()
    assert action["status"] == "pending" and action["payload"]["to"] == "accounts@halcyon-packaging.example"
    assert client.post("/api/followups", json={"file": "halcyon_invoice_sep2026.pdf"}, headers=DANIEL).status_code == 409
    assert client.post("/api/followups", json={"file": "scan_20260923_cedar.pdf"}, headers=DANIEL).status_code == 404
    rechecked = client.post("/api/invoices/check", json={}, headers=DANIEL).json()
    assert next(issue for issue in rechecked["issues"] if issue["file"] == "halcyon_invoice_sep2026.pdf")["drafted"] is True
    assert client.post("/api/followups", json={"file": "halcyon_invoice_sep2026.pdf"}, headers=DANIEL).status_code == 409
    assert not settings.outbox_path.exists()
    pending = client.get("/api/approvals", headers=DANIEL).json()["pending"]
    assert [item["id"] for item in pending] == [action["id"]]
    approved = client.post(f"/api/approvals/{action['id']}/approve", headers=DANIEL).json()
    assert approved["status"] == "executed" and approved["decided_by"] == "Daniel Reyes"
    record = json.loads(settings.outbox_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["simulated"] is True and record["approved_by"] == "Daniel Reyes"
    assert client.post(f"/api/approvals/{action['id']}/approve", headers=DANIEL).status_code == 409
    assert client.post(f"/api/approvals/{action['id']}/delete", headers=DANIEL).status_code == 404


def test_revoked_access_stops_the_comparison(client):
    assert client.post("/api/accounting", json={"enabled": False}, headers=DANIEL).json()["enabled"] is False
    data = client.post("/api/invoices/check", json={}, headers=DANIEL).json()
    assert data["compared"] is False and {row["result"] for row in data["rows"]} == {"Not compared"}
    assert client.post("/api/accounting", json={"enabled": True}, headers=DANIEL).json()["enabled"] is True
    events = [row["what"] for row in client.get("/api/activity", headers=DANIEL).json()["rows"]]
    assert events.count("accounting_access") == 2


def test_uploads_are_validated(client):
    rejected = client.post("/api/invoices/upload", files={"files": ("payload.exe", b"MZ", "application/octet-stream")}, headers=DANIEL).json()
    assert rejected["saved"] == [] and "not supported" in rejected["rejected"][0]
    disguised = client.post("/api/invoices/upload", files={"files": ("fake.pdf", b"<script>", "application/pdf")}, headers=DANIEL).json()
    assert disguised["saved"] == [] and "does not match" in disguised["rejected"][0]
    pdf = (settings.raw_dir / "invoices" / "coastal_office_invoice.pdf").read_bytes()
    saved = client.post("/api/invoices/upload", files={"files": ("../../new invoice.pdf", pdf, "application/pdf")}, headers=DANIEL).json()
    assert saved["saved"] == ["new_invoice.pdf"]
    assert "new_invoice.pdf" in client.get("/api/invoices", headers=DANIEL).json()["files"]


def test_service_errors_are_returned_in_plain_words(tmp_path, monkeypatch, retriever):
    client = make_client(tmp_path, monkeypatch, retriever, FailingChatModel())
    response = client.post("/api/ask", json={"question": "Bluestone payment terms"}, headers=DANIEL)
    assert response.status_code == 502
    assert "could not reach the AI service" in response.json()["detail"] and "503" not in response.json()["detail"]


def test_burst_of_questions_is_throttled(tmp_path, monkeypatch, retriever):
    client = make_client(tmp_path, monkeypatch, retriever, FakeChatModel(GroundedAnswer(found=False, answer="", citations=[])), rate_limit=1)
    assert client.post("/api/ask", json={"question": "first"}, headers=DANIEL).status_code == 200
    response = client.post("/api/ask", json={"question": "second"}, headers=DANIEL)
    assert response.status_code == 429 and "Too many questions" in response.json()["detail"]


def test_empty_question_is_rejected(client):
    response = client.post("/api/ask", json={"question": "   "}, headers=DANIEL)
    assert response.status_code == 400 and "enter a question" in response.json()["detail"]
