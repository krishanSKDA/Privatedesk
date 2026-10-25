import re
import sys
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import streamlit as st

from app.accounting.mock_books import (
    MATCH,
    AccountingAccessRevoked,
    AccountingConnection,
    MockQuickBooks,
    compare_invoices,
)
from app.approvals.gate import PENDING, SEND_EMAIL, ApprovalError, ApprovalGate, draft_follow_up
from app.config import settings
from app.extraction.extractor import ExtractionError, InvoiceExtractor, discover_invoices
from app.generation.llm import LLMError, RateLimitExceeded
from app.generation.rag_chain import RagChain
from app.ingestion.embedder import EmbeddingError
from app.ingestion.loader import FileRejectedError
from app.retrieval.retriever import RoleError
from app.retrieval.vector_store import VectorStoreError
from app.utils.logger import ActivityLog

DEMO_USERS = {
    "Daniel Reyes (Finance)": ("Daniel Reyes", "finance"),
    "Priya Nair (HR)": ("Priya Nair", "hr"),
}
PAGES = {"finance": ["Ask", "Invoices", "Approvals"], "hr": ["Ask"]}
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
STATUS = {
    "match": ("Matches the books", "ok"),
    "amount_mismatch": ("Amount differs", "alert"),
    "currency_mismatch": ("Currency differs", "alert"),
    "vendor_mismatch": ("Supplier differs", "alert"),
    "missing_from_books": ("Not in the books", "alert"),
    "needs_review": ("Needs a person", "alert"),
}
UPLOAD_DIR = settings.processed_dir / "uploads"
MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~<$])")

STYLE = """
<style>
[data-testid="stAppDeployButton"], [data-testid="stDecoration"], footer { display: none; }
.block-container { max-width: 880px; padding-top: 2.2rem; }
h1 { font-size: 1.7rem; font-weight: 700; letter-spacing: -0.01em; margin-bottom: 0.2rem; }
.pd-brand { font-size: 1.3rem; font-weight: 700; color: #1F2A44; letter-spacing: -0.01em; }
.pd-brand span { color: #2F5D8A; }
.pd-lede { color: #4A5670; font-size: 0.98rem; max-width: 62ch; margin-bottom: 1.2rem; }
.pd-stamp {
    display: inline-block; padding: 0.1rem 0.55rem; border: 2px solid currentColor;
    outline: 1px solid currentColor; outline-offset: 2px; border-radius: 3px;
    font-weight: 700; font-size: 0.82rem; letter-spacing: 0.02em; transform: rotate(-2deg);
}
.pd-stamp.ok { color: #2E7D5B; }
.pd-stamp.alert { color: #B23A3A; }
.pd-stamp.neutral { color: #2F5D8A; }
.pd-source { color: #4A5670; font-size: 0.85rem; }
</style>
"""


def plain(text):
    return MARKDOWN_SPECIAL.sub(r"\\\1", text or "")


def plain_lines(text):
    return "  \n".join(plain(line) for line in (text or "").splitlines())


def stamp(text, tone):
    st.markdown(f'<span class="pd-stamp {tone}">{text}</span>', unsafe_allow_html=True)


def lede(text):
    st.markdown(f'<p class="pd-lede">{text}</p>', unsafe_allow_html=True)


@st.cache_resource
def activity_log():
    return ActivityLog()


@st.cache_resource
def rag_chain():
    return RagChain(activity_log=activity_log())


@st.cache_resource
def invoice_extractor():
    return InvoiceExtractor()


def accounting_connection():
    return AccountingConnection()


def books():
    return MockQuickBooks(connection=accounting_connection())


def approval_gate():
    return ApprovalGate(activity_log=activity_log(), connection=accounting_connection())


def init_state():
    defaults = {"chats": {"finance": [], "hr": []}, "invoices": {}, "checks": [], "drafted": set(), "flash": []}
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def flash(kind, message):
    st.session_state.flash.append((kind, message))


def show_flash():
    for kind, message in st.session_state.flash:
        getattr(st, kind)(message)
    st.session_state.flash = []


def sidebar():
    st.sidebar.markdown('<div class="pd-brand">Private<span>Desk</span></div>', unsafe_allow_html=True)
    st.sidebar.caption("Answers from your company documents, kept private.")
    account = st.sidebar.selectbox("Signed in as", list(DEMO_USERS), key="account")
    user, role = DEMO_USERS[account]
    pages = PAGES[role]
    pending = len([action for action in approval_gate().pending() if action.role == role]) if "Approvals" in pages else 0
    page = pages[0]
    if len(pages) > 1:
        page = st.sidebar.radio(
            "Go to", pages, key=f"page-{role}", label_visibility="collapsed",
            format_func=lambda name: f"{name} ({pending})" if name == "Approvals" and pending else name,
        )
    st.sidebar.divider()
    admin_panel(user, role)
    return user, role, page


def admin_panel(user, role):
    with st.sidebar.expander("Privacy and admin"):
        st.markdown(
            f"**Sent to {settings.chat_provider.title()}:** your question, the document passages used to answer it, "
            "and invoices you check.\n\n**Kept on this computer:** the document index, access rules, approvals and the activity log."
        )
        if role != "finance":
            return
        st.markdown("**Accounting connection** (demo data, not a real QuickBooks account)")
        connection = accounting_connection()
        if connection.enabled:
            st.caption("Connected")
            if st.button("Revoke access", key="revoke"):
                connection.revoke(user)
                activity_log().log_access_change(user, role, enabled=False)
                st.rerun()
        else:
            st.caption(f"Access revoked by {connection.state()['changed_by']}")
            if st.button("Restore access", key="restore"):
                connection.restore(user)
                activity_log().log_access_change(user, role, enabled=True)
                st.rerun()
        if st.checkbox("Show activity log", key="show-log"):
            rows = [
                {"When": row["created_at"], "Who": row["user"], "What": row["event"], "Documents": row["documents"], "Result": row["outcome"]}
                for row in activity_log().recent(100)
                if row["role"] == role
            ]
            st.dataframe(rows, hide_index=True, width="stretch")


def ask_page(user, role):
    st.title("Ask about company documents", anchor=False)
    lede("Answers come only from documents your role can see, and each one shows where it came from.")
    chat = st.session_state.chats[role]
    question = st.chat_input("Ask a question")
    if not chat and not question:
        st.caption("Try one of these:")
        for suggestion in SUGGESTIONS[role]:
            if st.button(suggestion, key=f"suggest-{role}-{suggestion}"):
                chat.append(answer_message(suggestion, user, role))
                st.rerun()
    if question:
        chat.append(answer_message(question, user, role))
    for exchange in chat:
        render_exchange(exchange)
    if chat and st.button("Clear conversation", key=f"clear-{role}"):
        chat.clear()
        st.rerun()


def answer_message(question, user, role):
    exchange = {"question": question, "answer": None, "sources": [], "notice": None}
    try:
        with st.spinner("Searching your documents..."):
            result = rag_chain().answer(question, role, user=user)
    except RateLimitExceeded as error:
        exchange["notice"] = str(error)
        return exchange
    except ValueError as error:
        exchange["notice"] = str(error)
        return exchange
    except VectorStoreError:
        exchange["notice"] = "The documents have not been prepared yet. Run python run_ingestion.py, then ask again."
        return exchange
    except (LLMError, EmbeddingError, RoleError):
        exchange["notice"] = "The assistant could not reach the AI service. Check the connection and ask again in a minute."
        return exchange
    if result.found:
        exchange["answer"] = result.answer
        exchange["sources"] = [(chunk.title, chunk.page, chunk.text) for chunk in result.citations]
    else:
        exchange["answer"] = NOT_FOUND_TEXT
    return exchange


def render_exchange(exchange):
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(plain(exchange["question"]))
    with st.chat_message("assistant", avatar=":material/description:"):
        if exchange["notice"]:
            st.warning(exchange["notice"])
            return
        st.markdown(plain(exchange["answer"]))
        if not exchange["sources"]:
            return
        with st.expander(f"Sources ({len(exchange['sources'])})"):
            for title, page, text in exchange["sources"]:
                location = f", page {page}" if page else ""
                st.markdown(f'<p class="pd-source"><b>{escape(title)}</b>{location}</p>', unsafe_allow_html=True)
                st.markdown(plain_lines(text))


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


def invoices_page(user, role):
    st.title("Check invoices", anchor=False)
    lede(
        "Reads each invoice, compares it with the accounting records and flags anything a person should look at. "
        "Unclear details are left blank, never guessed."
    )
    with st.expander("Add invoices"):
        uploads = st.file_uploader("PDF, PNG or JPG", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True)
        include_holdout = st.checkbox("Include the unseen test invoices", key="include-holdout")
    paths = discover_invoices(include_holdout) + save_uploads(uploads)
    st.caption(f"{len(paths)} invoices ready to check.")
    if st.button("Check invoices", type="primary", disabled=not paths):
        check_invoices(paths, user, role)
    results = list(st.session_state.invoices.values())
    if results:
        render_invoice_table(results)
        render_issues(user, role)


def check_invoices(paths, user, role):
    progress = st.progress(0.0, text="Reading invoices...")
    for position, path in enumerate(paths, start=1):
        try:
            result = invoice_extractor().extract(path)
            st.session_state.invoices[path.name] = result
            activity_log().log_extraction(user, role, result)
        except FileRejectedError as error:
            flash("error", str(error))
        except (ExtractionError, LLMError, EmbeddingError):
            flash("error", f"{path.name} could not be read. Try checking it again.")
            activity_log().record(user, role, "invoice_extraction", documents=(path.name,), outcome="error")
        progress.progress(position / len(paths), text=f"Read {position} of {len(paths)} invoices")
    progress.empty()
    try:
        checks = compare_invoices(list(st.session_state.invoices.values()), books())
        activity_log().log_comparison(user, role, checks)
    except AccountingAccessRevoked:
        checks = []
        flash("warning", "The accounting connection is off, so invoices were read but not compared with the books.")
    st.session_state.checks = checks
    st.session_state.drafted = set()
    st.rerun()


def amount_text(result):
    amount, currency = result.value("amount"), result.value("currency")
    if amount is None:
        return "Check needed"
    try:
        figure = f"{Decimal(amount):,.2f}"
    except InvalidOperation:
        figure = amount
    return f"{currency} {figure}" if currency else f"{figure} (currency unclear)"


def render_invoice_table(results):
    checks = {item.file: item for item in st.session_state.checks}
    rows = []
    for result in results:
        check = checks.get(result.file)
        rows.append(
            {
                "Supplier": result.value("supplier") or "Check needed",
                "Invoice no.": result.value("invoice_number") or "Check needed",
                "Date": result.value("invoice_date") or "Check needed",
                "Amount": amount_text(result),
                "Result": STATUS[check.status][0] if check else "Not compared",
                "File": result.file,
            }
        )
    st.dataframe(rows, hide_index=True, width="stretch")


def render_issues(user, role):
    issues = [item for item in st.session_state.checks if item.status != MATCH]
    if not issues:
        return
    st.subheader(f"Needs attention ({len(issues)})", anchor=False)
    for item in issues:
        with st.container(border=True):
            label, tone = STATUS[item.status]
            stamp(label, tone)
            st.markdown(f"**{plain(item.vendor or item.file)}**")
            st.markdown(plain(item.message))
            result = st.session_state.invoices.get(item.file)
            if result and result.needs_review:
                with st.expander("What needs checking"):
                    for name in result.review_fields:
                        field = result.fields[name]
                        suggestion = f" Suggested: {field.candidate}." if field.candidate else ""
                        st.markdown(plain(f"{name.replace('_', ' ').capitalize()}: {'; '.join(field.reasons)}.{suggestion}"))
            if item.file in st.session_state.drafted:
                st.caption("Follow-up prepared. It is waiting in Approvals.")
            elif st.button("Prepare follow-up", key=f"draft-{item.file}"):
                prepare_follow_up(item, user, role)
                st.rerun()


def prepare_follow_up(item, user, role):
    try:
        vendor_email = books().vendor_email(item.vendor) if item.vendor else None
        action = draft_follow_up(approval_gate(), item, vendor_email, user, role)
    except (AccountingAccessRevoked, ApprovalError) as error:
        flash("error", str(error))
        return
    if action is None:
        flash("info", "There is no follow-up for this case yet.")
        return
    st.session_state.drafted.add(item.file)
    flash("success", "Follow-up prepared. Review it in Approvals.")


def approvals_page(user, role):
    gate = approval_gate()
    st.title("Approvals", anchor=False)
    lede("Nothing is sent or changed until you approve it. In this demo, approved actions are recorded on this computer instead of being sent.")
    pending = [action for action in gate.pending() if action.role == role]
    if not pending:
        st.info("Nothing is waiting for approval. Prepare follow-ups from Check invoices.")
    for action in pending:
        render_pending(gate, action, user, role)
    decided = [action for action in gate.list() if action.status != PENDING and action.role == role][:20]
    if decided:
        with st.expander(f"History ({len(decided)})"):
            for action in decided:
                stamp("Approved" if action.status == "executed" else "Rejected", "ok" if action.status == "executed" else "alert")
                st.caption(f"{plain(action.title)}. {action.decided_by}, {action.decided_at}")


def render_pending(gate, action, user, role):
    with st.container(border=True):
        stamp("Waiting for approval", "neutral")
        st.markdown(f"**{plain(action.title)}**")
        for warning in action.warnings:
            st.warning(warning)
        if action.kind == SEND_EMAIL:
            st.text(f"To: {action.payload['to']}\nSubject: {action.payload['subject']}\n\n{action.payload['body']}")
        else:
            details = {key: value for key, value in action.payload.items() if value}
            st.markdown("\n".join(f"- {plain(key.replace('_', ' ').capitalize())}: {plain(str(value))}" for key, value in details.items()))
        approve, reject = st.columns(2)
        if approve.button("Approve", key=f"approve-{action.id}", type="primary", width="stretch"):
            decide(gate.approve, action, user, role, "Approved. Recorded on this computer; nothing was sent.")
        if reject.button("Reject", key=f"reject-{action.id}", width="stretch"):
            decide(gate.reject, action, user, role, "Rejected. Nothing was sent.")


def decide(decision, action, user, role, message):
    try:
        decision(action.id, user, role)
        flash("success", message)
    except ApprovalError as error:
        flash("error", str(error))
    st.rerun()


PAGE_VIEWS = {"Ask": ask_page, "Invoices": invoices_page, "Approvals": approvals_page}


def main():
    st.set_page_config(page_title="PrivateDesk", layout="centered")
    st.markdown(STYLE, unsafe_allow_html=True)
    init_state()
    user, role, page = sidebar()
    show_flash()
    PAGE_VIEWS[page](user, role)


if __name__ == "__main__":
    main()
