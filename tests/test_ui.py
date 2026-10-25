import json

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from app.config import settings
from app.extraction.extractor import InvoiceExtractor
from app.generation.llm import RateLimiter
from app.generation.prompt import GroundedAnswer
from app.generation.rag_chain import RagChain
from app.utils.logger import ActivityLog
from tests.test_rag import (
    APEX_OVERCONFIDENT_REPLY,
    CEDAR_SCAN_REPLY,
    HALCYON_REPLY,
    FakeChatModel,
    chunks,
    embedder,
    extraction,
    field,
    ingested,
    retriever,
    store,
)

APP_PATH = str(settings.root_dir / "app" / "main.py")

COASTAL_REPLY = extraction(
    supplier=field("Coastal Office Supplies LLC", "Coastal Office Supplies LLC"),
    invoice_date=field("2026-09-18", "Invoice Date: 18/09/2026"),
    invoice_number=field("COS-88217", "Invoice No.: COS-88217"),
    amount=field("659.40", "USD 659.40"),
    currency=field("USD", "USD 659.40"),
)


class RoutingChatModel:
    provider = "test"
    model = "routing-fake"

    def generate_structured(self, instructions, user_input, schema):
        text = " ".join(part.get("text", "") for part in user_input)
        if any(part["type"] == "image" for part in user_input):
            return CEDAR_SCAN_REPLY
        if "HP-55102" in text:
            return HALCYON_REPLY
        if "COS-88217" in text:
            return COASTAL_REPLY
        return APEX_OVERCONFIDENT_REPLY


@pytest.fixture
def app(tmp_path, monkeypatch, retriever):
    for name, value in {
        "activity_db_path": tmp_path / "activity.sqlite",
        "outbox_path": tmp_path / "outbox.jsonl",
        "accounting_state_path": tmp_path / "accounting_connection.json",
    }.items():
        monkeypatch.setitem(settings.__dict__, name, value)
    reply = GroundedAnswer(found=True, answer="Net 30 days from the invoice date. ![x](https://evil.example/leak)", citations=["S1"])

    def chain_factory(activity_log=None):
        return RagChain(retriever=retriever, chat_model=FakeChatModel(reply), rate_limiter=RateLimiter(limit=1000), min_relevance=0.0, activity_log=activity_log)

    monkeypatch.setattr("app.generation.rag_chain.RagChain", chain_factory)
    monkeypatch.setattr("app.extraction.extractor.InvoiceExtractor", lambda: InvoiceExtractor(chat_model=RoutingChatModel()))
    st.cache_resource.clear()
    test = AppTest.from_file(APP_PATH, default_timeout=60).run()
    yield test
    st.cache_resource.clear()


def click(test, label):
    next(button for button in test.button if button.label == label).click()
    return test.run()


def markdown_text(test):
    return "\n".join(item.value for item in test.markdown)


def test_app_starts_with_three_tabs_and_a_mock_label(app):
    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Ask", "Invoices", "Review"]
    assert any("not a real QuickBooks integration" in caption.value for caption in app.sidebar.caption)


def test_ask_shows_an_answer_with_its_source_and_logs_it(app, retriever):
    question = "What are the Bluestone payment terms?"
    app.text_area(key="question_input").input(question)
    click(app, "Ask")
    assert not app.exception
    assert "Net 30 days" in markdown_text(app)
    cited = retriever.retrieve(question, "finance")[0].citation
    assert any(expander.label.startswith(cited) for expander in app.expander)
    rows = ActivityLog().recent(5)
    assert any(row["question"] == "What are the Bluestone payment terms?" and row["role"] == "finance" for row in rows)


def test_model_output_cannot_render_markdown_images(app):
    app.text_area(key="question_input").input("Bluestone payment terms")
    click(app, "Ask")
    text = markdown_text(app)
    assert "![x](https://evil.example/leak)" not in text
    assert "\\!\\[x\\]" in text


def test_hr_user_cannot_open_invoices(app):
    app.sidebar.selectbox[0].select("hr").run()
    assert any("only available to the Finance role" in info.value for info in app.info)
    assert not any(button.label == "Extract fields" for button in app.button)
    assert not any(button.label == "Revoke access" for button in app.sidebar.button)


def test_invoice_flow_extracts_compares_drafts_and_approves(app):
    click(app, "Extract fields")
    assert not app.exception
    table = app.dataframe[0].value
    apex = table[table["File"] == "apex_invoice.pdf"].iloc[0]
    assert apex["Invoice no."] == "needs review" and apex["Status"] == "Needs review"
    click(app, "Compare with mock QuickBooks")
    warnings = [item.value for item in app.warning]
    assert any("halcyon_invoice_sep2026.pdf: Amount mismatch" in value for value in warnings)
    assert any("coastal_office_invoice.pdf: Missing from the books" in value for value in warnings)
    next(button for button in app.button if button.key == "draft-halcyon_invoice_sep2026.pdf").click()
    app.run()
    assert any("waiting for approval" in item.value for item in app.success)
    assert not settings.outbox_path.exists()
    next(button for button in app.button if button.key and button.key.startswith("approve-")).click()
    app.run()
    record = json.loads(settings.outbox_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["simulated"] is True and record["approved_by"] == "Dana Lee"
    assert record["payload"]["to"] == "accounts@halcyon-packaging.example"


def test_revoking_access_blocks_the_books_comparison(app):
    click(app, "Revoke access")
    assert any("Access revoked" in item.value for item in app.sidebar.error)
    click(app, "Extract fields")
    click(app, "Compare with mock QuickBooks")
    assert any("revoked" in item.value for item in app.error)
    click(app, "Restore access")
    assert any("Connected" in item.value for item in app.sidebar.success)
