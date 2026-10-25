import json

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from app.config import settings
from app.extraction.extractor import InvoiceExtractor
from app.generation.llm import LLMError, RateLimiter
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
ANSWER = "Net 30 days from the invoice date. ![x](https://evil.example/leak)"

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


class FailingChatModel:
    provider = "test"
    model = "failing-fake"

    def generate_structured(self, instructions, user_input, schema):
        raise LLMError("Chat request failed: ServerError (HTTP 503)")


def start_app(tmp_path, monkeypatch, retriever, chat_model):
    for name, value in {
        "activity_db_path": tmp_path / "activity.sqlite",
        "outbox_path": tmp_path / "outbox.jsonl",
        "accounting_state_path": tmp_path / "accounting_connection.json",
    }.items():
        monkeypatch.setitem(settings.__dict__, name, value)

    def chain_factory(activity_log=None):
        return RagChain(retriever=retriever, chat_model=chat_model, rate_limiter=RateLimiter(limit=1000), min_relevance=0.0, activity_log=activity_log)

    monkeypatch.setattr("app.generation.rag_chain.RagChain", chain_factory)
    monkeypatch.setattr("app.extraction.extractor.InvoiceExtractor", lambda: InvoiceExtractor(chat_model=RoutingChatModel()))
    st.cache_resource.clear()
    return AppTest.from_file(APP_PATH, default_timeout=60).run()


@pytest.fixture
def app(tmp_path, monkeypatch, retriever):
    reply = GroundedAnswer(found=True, answer=ANSWER, citations=["S1"])
    yield start_app(tmp_path, monkeypatch, retriever, FakeChatModel(reply))
    st.cache_resource.clear()


def click(test, label=None, key=None):
    next(button for button in test.button if (key and button.key == key) or (label and button.label == label)).click()
    return test.run()


def go_to(test, page):
    test.sidebar.radio[0].set_value(page)
    return test.run()


def ask(test, question):
    test.chat_input[0].set_value(question)
    return test.run()


def chat_text(test):
    return "\n".join(item.value for message in test.chat_message for item in message.markdown)


def page_markdown(test):
    return "\n".join(item.value for item in test.markdown)


def test_app_opens_on_the_chat_with_simple_navigation(app):
    assert not app.exception
    assert [title.value for title in app.title] == ["Ask about company documents"]
    assert app.sidebar.radio[0].options == ["Ask", "Invoices", "Approvals"]
    assert len(app.chat_input) == 1
    assert "relevance" not in page_markdown(app).lower()


def test_chat_answer_shows_its_source_by_document_title(app, retriever):
    question = "What are the Bluestone payment terms?"
    ask(app, question)
    assert not app.exception
    assert "Net 30 days" in chat_text(app)
    assert any(expander.label == "Sources (1)" for expander in app.expander)
    title = retriever.retrieve(question, "finance")[0].title
    assert any(title in item.value for expander in app.expander for item in expander.markdown)
    assert not any("relevance" in expander.label for expander in app.expander)
    rows = ActivityLog().recent(5)
    assert any(row["question"] == question and row["user"] == "Daniel Reyes" for row in rows)


def test_suggestion_starts_the_conversation_and_disappears(app):
    click(app, label="What are the payment terms in the Bluestone freight agreement?")
    assert "Net 30 days" in chat_text(app)
    assert not any(button.key and button.key.startswith("suggest-") for button in app.button)


def test_model_output_cannot_render_markdown_images(app):
    ask(app, "Bluestone payment terms")
    text = chat_text(app)
    assert "![x](https://evil.example/leak)" not in text
    assert "\\!\\[x\\]" in text


def test_service_errors_are_shown_in_plain_words(tmp_path, monkeypatch, retriever):
    app = start_app(tmp_path, monkeypatch, retriever, FailingChatModel())
    ask(app, "Bluestone payment terms")
    warnings = [item.value for item in app.warning]
    assert any("could not reach the AI service" in value for value in warnings)
    assert not any("HTTP 503" in value for value in warnings)
    st.cache_resource.clear()


def test_hr_user_sees_only_the_chat(app):
    app.sidebar.selectbox[0].select("Priya Nair (HR)").run()
    assert not app.exception
    assert len(app.sidebar.radio) == 0
    assert [title.value for title in app.title] == ["Ask about company documents"]
    assert not any(button.label == "Revoke access" for button in app.button)


def test_invoice_check_prepares_a_follow_up_that_waits_for_approval(app):
    go_to(app, "Invoices")
    click(app, label="Check invoices")
    assert not app.exception
    table = app.dataframe[0].value
    results = dict(zip(table["File"], table["Result"]))
    assert results["halcyon_invoice_sep2026.pdf"] == "Amount differs"
    assert results["coastal_office_invoice.pdf"] == "Not in the books"
    assert results["apex_invoice.pdf"] == "Needs a person"
    assert results["scan_20260923_cedar.pdf"] == "Matches the books"
    assert table[table["File"] == "apex_invoice.pdf"].iloc[0]["Invoice no."] == "Check needed"
    click(app, key="draft-halcyon_invoice_sep2026.pdf")
    assert any("Review it in Approvals" in item.value for item in app.success)
    assert not settings.outbox_path.exists()
    go_to(app, "Approvals")
    assert "Approvals (1)" in [app.sidebar.radio[0].format_func(option) for option in app.sidebar.radio[0].options]
    click(app, key=next(button.key for button in app.button if button.key and button.key.startswith("approve-")))
    record = json.loads(settings.outbox_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["simulated"] is True and record["approved_by"] == "Daniel Reyes"
    assert record["payload"]["to"] == "accounts@halcyon-packaging.example"
    assert any("nothing was sent" in item.value for item in app.success)


def test_revoking_access_stops_the_books_comparison(app):
    click(app, label="Revoke access")
    assert any(button.label == "Restore access" for button in app.button)
    go_to(app, "Invoices")
    click(app, label="Check invoices")
    assert any("accounting connection is off" in item.value for item in app.warning)
    assert set(app.dataframe[0].value["Result"]) == {"Not compared"}
    click(app, label="Restore access")
    assert any(button.label == "Revoke access" for button in app.button)
