import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import streamlit as st

from app.accounting.mock_books import (
    MATCH,
    MOCK_LABEL,
    AccountingAccessRevoked,
    AccountingConnection,
    MockQuickBooks,
    compare_invoices,
)
from app.approvals.gate import PENDING, SEND_EMAIL, ApprovalError, ApprovalGate, draft_follow_up
from app.config import settings
from app.extraction.extractor import ExtractionError, InvoiceExtractor, discover_invoices
from app.extraction.schemas import FIELD_NAMES
from app.generation.llm import LLMError, RateLimitExceeded
from app.generation.rag_chain import MODEL_NOT_FOUND, NO_RELEVANT_PASSAGES, NO_VALID_CITATIONS, RagChain
from app.ingestion.embedder import EmbeddingError
from app.ingestion.loader import FileRejectedError
from app.retrieval.retriever import RoleError
from app.retrieval.vector_store import VectorStore, VectorStoreError
from app.utils.logger import ActivityLog

ROLE_LABELS = {"finance": "Finance", "hr": "HR"}
FIELD_LABELS = {
    "supplier": "Supplier",
    "invoice_date": "Date",
    "invoice_number": "Invoice no.",
    "amount": "Amount",
    "currency": "Currency",
}
REFUSAL_REASONS = {
    NO_RELEVANT_PASSAGES: "No passage you can access was relevant enough to answer from.",
    MODEL_NOT_FOUND: "The passages you can access do not contain the answer.",
    NO_VALID_CITATIONS: "The answer could not be tied to a source passage, so it was withheld.",
}
STATUS_LABELS = {
    "match": "Matches the books",
    "amount_mismatch": "Amount mismatch",
    "currency_mismatch": "Currency mismatch",
    "vendor_mismatch": "Vendor mismatch",
    "missing_from_books": "Missing from the books",
    "needs_review": "Needs review",
}
SAMPLE_QUESTIONS = {
    "finance": [
        "What are the payment terms in the Bluestone freight agreement?",
        "Who has to approve an expense claim of USD 1,800?",
        "What was the revenue in Q3 2026?",
    ],
    "hr": [
        "How many unused leave days can be carried over?",
        "When is a medical certificate needed for sick leave?",
        "What is the liability cap in the Bluestone agreement?",
    ],
}
UPLOAD_DIR = settings.processed_dir / "uploads"
MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~<$])")


def plain(text):
    return MARKDOWN_SPECIAL.sub(r"\\\1", text or "")


@st.cache_resource
def activity_log():
    return ActivityLog()


@st.cache_resource
def rag_chain():
    return RagChain(activity_log=activity_log())


@st.cache_resource
def invoice_extractor():
    return InvoiceExtractor()


@st.cache_resource
def vector_store():
    return VectorStore()


def accounting_connection():
    return AccountingConnection()


def books():
    return MockQuickBooks(connection=accounting_connection())


def approval_gate():
    return ApprovalGate(activity_log=activity_log(), connection=accounting_connection())


def init_state():
    for key, default in (("history", []), ("extractions", {}), ("discrepancies", []), ("drafted", set()), ("flash", [])):
        st.session_state.setdefault(key, default)


def flash(kind, message):
    st.session_state.flash.append((kind, message))


def show_flash():
    for kind, message in st.session_state.flash:
        getattr(st, kind)(message)
    st.session_state.flash = []


def sidebar():
    st.sidebar.title("PrivateDesk")
    st.sidebar.caption("Document and invoice assistant. Prototype with synthetic demo data only.")
    user = st.sidebar.text_input("Your name", value="Dana Lee").strip() or "anonymous"
    role = st.sidebar.selectbox("Role", settings.roles, format_func=ROLE_LABELS.get)
    st.sidebar.caption("Roles are picked here for the demo. A real deployment would take them from a login.")
    accounting_panel(user, role)
    index_panel()
    data_flow_panel()
    return user, role


def accounting_panel(user, role):
    st.sidebar.subheader("Accounting connection")
    st.sidebar.caption(MOCK_LABEL)
    connection = accounting_connection()
    if connection.enabled:
        st.sidebar.success("Connected (mock)")
    else:
        state = connection.state()
        st.sidebar.error(f"Access revoked by {state['changed_by']} at {state['changed_at']}")
    if role != "finance":
        st.sidebar.caption("Only Finance can change this connection.")
        return
    if connection.enabled and st.sidebar.button("Revoke access", type="primary"):
        connection.revoke(user)
        activity_log().log_access_change(user, role, enabled=False)
        st.rerun()
    if not connection.enabled and st.sidebar.button("Restore access"):
        connection.restore(user)
        activity_log().log_access_change(user, role, enabled=True)
        st.rerun()


def index_panel():
    st.sidebar.subheader("Document index")
    try:
        store = vector_store()
        info = store.index_info()
        st.sidebar.caption(
            f"{store.count()} chunks | {info.get('embedding_provider')}/{info.get('embedding_model')} | built {info.get('built_at')}"
        )
    except VectorStoreError as error:
        st.sidebar.warning(str(error))


def data_flow_panel():
    with st.sidebar.expander("What leaves this machine"):
        st.markdown(
            f"**Sent to {settings.chat_provider.title()}:** questions, the retrieved passages used to answer them, "
            "and invoice text or page images for extraction.\n\n"
            "**Stays on this machine:** the vector index, role rules, activity log, approvals, "
            "the outbox and the mock accounting data."
        )


def ask_tab(user, role):
    st.subheader("Ask the documents")
    st.caption(f"Answers use only documents the {ROLE_LABELS[role]} role may see, and show the passages they come from.")
    columns = st.columns(len(SAMPLE_QUESTIONS[role]))
    for column, sample in zip(columns, SAMPLE_QUESTIONS[role]):
        if column.button(sample, key=f"sample-{role}-{sample}"):
            st.session_state["question_input"] = sample
    with st.form("ask-form"):
        question = st.text_area("Question", key="question_input", height=80, max_chars=settings.max_question_chars)
        submitted = st.form_submit_button("Ask", type="primary")
    if submitted:
        ask(question, user, role)
    for asked_role, result in st.session_state.history:
        if asked_role == role:
            render_answer(result)


def ask(question, user, role):
    try:
        with st.spinner("Searching the documents..."):
            result = rag_chain().answer(question, role, user=user)
        st.session_state.history.insert(0, (role, result))
    except (RateLimitExceeded, ValueError) as error:
        st.warning(str(error))
    except (LLMError, EmbeddingError, VectorStoreError, RoleError) as error:
        st.error(str(error))


def render_answer(result):
    with st.container(border=True):
        st.markdown(f"**Question:** {plain(result.question)}")
        if not result.found:
            st.warning(result.answer)
            st.caption(REFUSAL_REASONS.get(result.reason, result.reason))
            return
        st.markdown(plain(result.answer))
        st.caption("Sources")
        for chunk in result.citations:
            with st.expander(f"{chunk.citation} | relevance {chunk.score:.2f}"):
                st.markdown(plain(chunk.text))


def safe_upload_name(name):
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).name)
    return stem.lstrip(".") or "upload"


def save_uploads(files):
    paths = []
    for file in files or []:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        path = UPLOAD_DIR / safe_upload_name(file.name)
        path.write_bytes(file.getvalue())
        paths.append(path)
    return paths


def invoices_tab(user, role):
    st.subheader("Invoices")
    if role != "finance":
        st.info("Invoices are only available to the Finance role.")
        return
    st.caption(
        f"Invoice text or page images are sent to {settings.chat_provider.title()} for extraction. "
        "Missing or uncertain fields are left empty and marked for review, never guessed."
    )
    include_holdout = st.checkbox("Include held-out invoices (final evaluation only)")
    uploads = st.file_uploader("Add invoices (PDF, PNG or JPG)", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)
    paths = discover_invoices(include_holdout) + save_uploads(uploads)
    selected = st.multiselect("Invoices to extract", paths, default=paths, format_func=lambda path: path.name)
    if st.button("Extract fields", type="primary", disabled=not selected):
        extract(selected, user, role)
    results = list(st.session_state.extractions.values())
    if not results:
        return
    render_extractions(results)
    comparison_section(results, user, role)


def extract(paths, user, role):
    progress = st.progress(0.0, text="Extracting...")
    for position, path in enumerate(paths, start=1):
        try:
            result = invoice_extractor().extract(path)
            st.session_state.extractions[path.name] = result
            activity_log().log_extraction(user, role, result)
        except (FileRejectedError, ExtractionError, LLMError, EmbeddingError) as error:
            st.error(str(error))
            activity_log().record(user, role, "invoice_extraction", documents=(path.name,), outcome="error")
        progress.progress(position / len(paths), text=f"Extracted {position} of {len(paths)}")
    st.session_state.discrepancies = []


def render_extractions(results):
    rows = [
        {
            "File": result.file,
            **{FIELD_LABELS[name]: result.value(name) or "needs review" for name in FIELD_NAMES},
            "Status": "Needs review" if result.needs_review else "OK",
        }
        for result in results
    ]
    st.dataframe(rows, hide_index=True, width="stretch")
    for result in results:
        if not result.needs_review:
            continue
        with st.expander(f"Why {result.file} needs review"):
            for name in result.review_fields:
                field = result.fields[name]
                st.markdown(f"**{FIELD_LABELS[name]}**: {plain('; '.join(field.reasons))}")
                if field.candidate:
                    st.caption(f"Suggested value for the reviewer: {plain(field.candidate)} | read from: {plain(field.evidence) or '-'}")


def comparison_section(results, user, role):
    st.subheader("Compare with the books")
    st.caption(MOCK_LABEL)
    if st.button("Compare with mock QuickBooks"):
        try:
            discrepancies = compare_invoices(results, books())
            st.session_state.discrepancies = discrepancies
            activity_log().log_comparison(user, role, discrepancies)
        except AccountingAccessRevoked as error:
            st.error(str(error))
    for item in st.session_state.discrepancies:
        render_discrepancy(item, user, role)


def render_discrepancy(item, user, role):
    with st.container(border=True):
        label = STATUS_LABELS.get(item.status, item.status)
        if item.status == MATCH:
            st.success(f"{item.file}: {label}")
        else:
            st.warning(f"{item.file}: {label}")
        st.markdown(plain(item.message))
        if item.status == MATCH:
            return
        if item.file in st.session_state.drafted:
            st.caption("A follow-up has been drafted and is waiting in the Review tab.")
            return
        if st.button("Draft follow-up", key=f"draft-{item.file}"):
            draft(item, user, role)
            st.rerun()


def draft(item, user, role):
    try:
        vendor_email = books().vendor_email(item.vendor) if item.vendor else None
        action = draft_follow_up(approval_gate(), item, vendor_email, user, role)
    except (AccountingAccessRevoked, ApprovalError) as error:
        flash("error", str(error))
        return
    if action is None:
        flash("info", f"There is no follow-up template for {item.file}.")
        return
    st.session_state.drafted.add(item.file)
    flash("success", f"Drafted: {action.title}. It is waiting for approval in the Review tab.")


def review_tab(user, role):
    gate = approval_gate()
    pending = [action for action in gate.pending() if action.role == role]
    st.subheader(f"Waiting for approval ({len(pending)})")
    st.caption(
        "Nothing is sent or changed until a named person approves it. Approved actions are simulated: "
        "they are written to a local outbox file and never leave this machine."
    )
    if not pending:
        st.info("No drafted actions are waiting.")
    for action in pending:
        render_action(gate, action, user, role)
    decided = [action for action in gate.list() if action.status != PENDING and action.role == role][:20]
    with st.expander(f"Decided actions ({len(decided)})"):
        st.dataframe(
            [{"Action": a.title, "Status": a.status, "By": a.decided_by, "At": a.decided_at, "Note": a.decision_note} for a in decided],
            hide_index=True, width="stretch",
        )
    with st.expander("Activity log"):
        st.caption("Logs record who asked what, the document names used and each decision. They never store document text.")
        rows = [row for row in activity_log().recent(200) if row["role"] == role]
        st.dataframe(rows, hide_index=True, width="stretch")


def render_action(gate, action, user, role):
    with st.container(border=True):
        st.markdown(f"**{plain(action.title)}**")
        st.caption(f"Requested by {action.created_by} | {action.created_at} | source: {action.source or '-'}")
        for warning in action.warnings:
            st.warning(warning)
        if action.kind == SEND_EMAIL:
            st.text(f"To: {action.payload['to']}\nSubject: {action.payload['subject']}\n\n{action.payload['body']}")
        else:
            st.json(action.payload)
        note = st.text_input("Note (optional)", key=f"note-{action.id}")
        approve_column, reject_column = st.columns(2)
        if approve_column.button("Approve", key=f"approve-{action.id}", type="primary"):
            decide(gate.approve, action, user, role, note, "Approved. The action was simulated and written to the local outbox.")
        if reject_column.button("Reject", key=f"reject-{action.id}"):
            decide(gate.reject, action, user, role, note, "Rejected. Nothing was sent.")


def decide(decision, action, user, role, note, message):
    try:
        decision(action.id, user, role, note or None)
        flash("success", message)
    except ApprovalError as error:
        flash("error", str(error))
    st.rerun()


def main():
    st.set_page_config(page_title="PrivateDesk", layout="wide")
    init_state()
    user, role = sidebar()
    st.title("PrivateDesk")
    show_flash()
    ask_panel, invoices_panel, review_panel = st.tabs(["Ask", "Invoices", "Review"])
    with ask_panel:
        ask_tab(user, role)
    with invoices_panel:
        invoices_tab(user, role)
    with review_panel:
        review_tab(user, role)


if __name__ == "__main__":
    main()
