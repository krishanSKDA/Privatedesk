"use strict";

const PAGE_NAMES = { ask: "Ask", invoices: "Invoices", approvals: "Approvals" };
const STORAGE_KEY = "privatedesk-user";

const state = {
  provider: "the AI provider",
  users: [],
  user: null,
  page: "ask",
  chats: {},
  check: null,
  includeHoldout: false,
  pending: 0,
  busy: false,
};

const $ = (selector) => document.querySelector(selector);

function el(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (value === false || value === null || value === undefined) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function readStored(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    return;
  }
}

let toastTimer = null;

function toast(message, tone = "info") {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast show${tone === "error" ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.className = "toast"; }, 4200);
}

async function api(path, options = {}) {
  const headers = { "X-Demo-User": state.user.id };
  let body = options.body;
  if (body && !(body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(body);
  }
  const response = await fetch(path, { method: options.method || "GET", headers, body });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "Something went wrong. Try again.";
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return data;
}

function money(amount, currency) {
  if (amount === null || amount === undefined) return null;
  const figure = Number(amount).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return currency ? `${currency} ${figure}` : `${figure} (currency unclear)`;
}

function stamp(label, tone, extra = "") {
  return el("span", { class: `stamp ${tone} ${extra}`.trim(), text: label });
}

function header(title, lede) {
  return el("header", { class: "page-header" }, el("h1", { text: title }), el("p", { text: lede }));
}

function showPage(...children) {
  const main = $("#main");
  main.replaceChildren(el("div", { class: `page page-${state.page}` }, ...children));
}

function renderUsers() {
  const select = $("#user-select");
  select.replaceChildren(...state.users.map((user) => el("option", { value: user.id, text: `${user.name} (${user.team})` })));
  select.value = state.user.id;
  select.onchange = () => {
    state.user = state.users.find((user) => user.id === select.value);
    writeStored(STORAGE_KEY, state.user.id);
    state.check = null;
    navigate("ask");
    renderAdmin();
  };
}

function renderNav() {
  const nav = $("#nav");
  const pages = state.user.pages;
  nav.hidden = pages.length < 2;
  nav.replaceChildren(
    ...pages.map((page) =>
      el(
        "button",
        { type: "button", "aria-current": state.page === page ? "page" : false, onclick: () => navigate(page) },
        el("span", { text: PAGE_NAMES[page] }),
        page === "approvals" && state.pending ? el("span", { class: "badge", text: state.pending, "aria-label": `${state.pending} waiting` }) : null,
      ),
    ),
  );
}

async function refreshPending() {
  if (!state.user.pages.includes("approvals")) {
    state.pending = 0;
  } else {
    try {
      state.pending = (await api("/api/approvals")).pending.length;
    } catch {
      state.pending = 0;
    }
  }
  renderNav();
}

function navigate(page) {
  state.page = state.user.pages.includes(page) ? page : "ask";
  if (window.location.hash.slice(1) !== state.page) history.replaceState(null, "", `#${state.page}`);
  renderNav();
  const views = { ask: renderAsk, invoices: renderInvoices, approvals: renderApprovals };
  views[state.page]();
  $("#main").focus({ preventScroll: true });
  refreshPending();
}

function chatFor() {
  state.chats[state.user.id] = state.chats[state.user.id] || [];
  return state.chats[state.user.id];
}

function renderAsk() {
  const chat = chatFor();
  const log = el("div", { class: "chat-log", id: "chat-log", "aria-live": "polite" });
  const input = el("textarea", { id: "question", rows: 1, maxlength: 1000, placeholder: "Ask a question", "aria-label": "Your question" });
  const send = el("button", { type: "submit", class: "primary", text: "Send" });
  const form = el("form", { class: "composer", onsubmit: (event) => { event.preventDefault(); ask(input.value, input, send); } }, input, send);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  });
  showPage(
    header("Ask about company documents", "Answers come only from documents your role can see, and each one shows where it came from."),
    log,
    form,
  );
  if (!chat.length) {
    log.append(
      el(
        "div",
        { class: "suggestions" },
        el("p", { text: "Try one of these:" }),
        state.user.suggestions.map((text) => el("button", { type: "button", class: "suggestion", text, onclick: () => ask(text, input, send) })),
      ),
    );
  }
  chat.forEach((exchange) => log.append(exchangeView(exchange)));
  if (chat.length) log.append(clearButton());
  input.focus();
}

function clearButton() {
  return el("button", { type: "button", class: "clear-chat", text: "Clear conversation", onclick: () => { chatFor().length = 0; renderAsk(); } });
}

async function ask(text, input, send) {
  const question = text.trim();
  if (!question || state.busy) return;
  state.busy = true;
  send.disabled = true;
  input.value = "";
  input.style.height = "auto";
  const log = $("#chat-log");
  log.querySelector(".suggestions")?.remove();
  log.querySelector(".clear-chat")?.remove();
  const exchange = { question, pending: true };
  chatFor().push(exchange);
  let view = exchangeView(exchange);
  log.append(view);
  view.scrollIntoView({ block: "end", behavior: "smooth" });
  try {
    const reply = await api("/api/ask", { method: "POST", body: { question } });
    Object.assign(exchange, { pending: false, found: reply.found, answer: reply.answer, sources: reply.sources });
  } catch (error) {
    Object.assign(exchange, { pending: false, error: error.message });
  }
  if (state.page === "ask" && view.isConnected) {
    const updated = exchangeView(exchange);
    view.replaceWith(updated);
    view = updated;
    log.append(clearButton());
    view.scrollIntoView({ block: "end", behavior: "smooth" });
  }
  state.busy = false;
  send.disabled = false;
  input.focus();
}

function exchangeView(exchange) {
  const asked = el("div", { class: "message from-user" }, el("p", { text: exchange.question }));
  let reply;
  if (exchange.pending) {
    reply = el(
      "div",
      { class: "message from-assistant" },
      el("span", { class: "typing", "aria-hidden": "true" }, el("i"), el("i"), el("i")),
      el("span", { class: "visually-hidden", text: "Searching your documents" }),
    );
  } else if (exchange.error) {
    reply = el("div", { class: "message from-assistant notice" }, el("p", { text: exchange.error }));
  } else {
    reply = el(
      "div",
      { class: `message from-assistant${exchange.found ? "" : " not-found"}` },
      el("p", { text: exchange.answer }),
      exchange.sources && exchange.sources.length ? sourcesView(exchange.sources) : null,
    );
  }
  return el("div", { class: "exchange" }, asked, reply);
}

function sourcesView(sources) {
  return el(
    "details",
    { class: "sources" },
    el("summary", { text: `Sources (${sources.length})` }),
    el(
      "ol",
      {},
      sources.map((source) =>
        el("li", {}, el("strong", { text: source.title }), source.page ? `, page ${source.page}` : "", el("blockquote", { text: source.text })),
      ),
    ),
  );
}

async function renderInvoices() {
  const count = el("span", { class: "hint", text: "Finding invoices..." });
  const checkButton = el("button", { type: "button", class: "primary", text: "Check invoices", disabled: true });
  const results = el("div", { id: "invoice-results" });
  const files = el("input", { type: "file", accept: ".pdf,.png,.jpg,.jpeg", multiple: true, "aria-label": "Invoice files" });
  const uploadButton = el("button", { type: "button", class: "secondary", text: "Upload" });
  const holdout = el("input", { type: "checkbox", checked: state.includeHoldout });
  const tools = el(
    "details",
    { class: "panel" },
    el("summary", { text: "Add invoices" }),
    el(
      "div",
      { class: "panel-body" },
      el("div", { class: "toolbar" }, files, uploadButton),
      el("label", {}, holdout, "Include the unseen test invoices"),
    ),
  );
  showPage(
    header("Check invoices", "Reads each invoice, compares it with the accounting records and flags anything a person should look at. Unclear details are left blank, never guessed."),
    tools,
    el("div", { class: "toolbar" }, checkButton, count),
    results,
  );

  const loadCount = async () => {
    try {
      const data = await api(`/api/invoices?include_holdout=${state.includeHoldout}`);
      count.textContent = `${data.files.length} invoices ready to check.`;
      checkButton.disabled = data.files.length === 0;
    } catch (error) {
      count.textContent = error.message;
    }
  };

  holdout.onchange = () => { state.includeHoldout = holdout.checked; loadCount(); };

  uploadButton.onclick = async () => {
    if (!files.files.length) {
      toast("Choose one or more invoice files first.");
      return;
    }
    const form = new FormData();
    [...files.files].forEach((file) => form.append("files", file));
    uploadButton.disabled = true;
    try {
      const data = await api("/api/invoices/upload", { method: "POST", body: form });
      if (data.rejected.length) toast(data.rejected.join(" "), "error");
      else toast(`Added ${data.saved.length} invoice${data.saved.length === 1 ? "" : "s"}.`);
      files.value = "";
      loadCount();
    } catch (error) {
      toast(error.message, "error");
    }
    uploadButton.disabled = false;
  };

  checkButton.onclick = async () => {
    checkButton.disabled = true;
    checkButton.textContent = "Reading invoices...";
    results.replaceChildren(el("p", { class: "hint", text: "Reading each invoice and comparing it with the books. This takes about a minute." }));
    try {
      state.check = await api("/api/invoices/check", { method: "POST", body: { include_holdout: state.includeHoldout } });
      state.check.errors.forEach((message) => toast(message, "error"));
      renderCheck(results);
    } catch (error) {
      results.replaceChildren();
      toast(error.message, "error");
    }
    checkButton.disabled = false;
    checkButton.textContent = "Check invoices";
  };

  await loadCount();
  if (!state.check) {
    try {
      state.check = await api("/api/invoices/check");
    } catch {
      state.check = null;
    }
  }
  if (state.check && state.page === "invoices") renderCheck(results);
}

function cell(value, className = "") {
  return value === null || value === undefined
    ? el("td", { class: "missing", text: "Check needed" })
    : el("td", { class: className, text: value });
}

function renderCheck(container) {
  const check = state.check;
  const parts = [];
  if (!check.compared) {
    parts.push(el("div", { class: "notice-bar", text: "The accounting connection is off, so invoices were read but not compared with the books." }));
  }
  parts.push(
    el(
      "div",
      { class: "table-wrap" },
      el(
        "table",
        {},
        el("thead", {}, el("tr", {}, ["Supplier", "Invoice no.", "Date", "Amount", "Result"].map((name) => el("th", { scope: "col", text: name })))),
        el(
          "tbody",
          {},
          check.rows.map((row) =>
            el(
              "tr",
              {},
              el(
                "td",
                {},
                el("span", { class: row.supplier ? "supplier" : "supplier missing", text: row.supplier || "Check needed" }),
                el("span", { class: "file", text: row.file }),
              ),
              cell(row.invoice_number, "number"),
              cell(row.date, "number"),
              cell(money(row.amount, row.currency), "number"),
              el("td", {}, el("span", { class: `pill ${row.tone}`, text: row.result })),
            ),
          ),
        ),
      ),
    ),
  );
  if (check.issues.length) {
    parts.push(el("h2", { class: "section-title", text: `Needs attention (${check.issues.length})` }));
    parts.push(el("div", { class: "issues" }, check.issues.map(issueView)));
  }
  container.replaceChildren(...parts);
}

function issueView(issue) {
  const footer = issue.drafted
    ? el("p", { class: "done", text: "Follow-up prepared. It is waiting in Approvals." })
    : el("button", { type: "button", class: "secondary", text: "Prepare follow-up", onclick: (event) => prepareFollowUp(issue, event.currentTarget) });
  return el(
    "article",
    { class: "item" },
    stamp(issue.label, issue.tone),
    el("h3", { text: issue.vendor }),
    el("p", { text: issue.message }),
    issue.review.length
      ? el(
          "details",
          {},
          el("summary", { text: "What needs checking" }),
          el(
            "ul",
            {},
            issue.review.map((item) =>
              el("li", { text: `${item.field}: ${item.reasons.join("; ")}.${item.suggestion ? ` Suggested: ${item.suggestion}.` : ""}` }),
            ),
          ),
        )
      : null,
    footer,
  );
}

async function prepareFollowUp(issue, button) {
  button.disabled = true;
  try {
    await api("/api/followups", { method: "POST", body: { file: issue.file } });
    issue.drafted = true;
    toast("Follow-up prepared. Review it in Approvals.");
    button.replaceWith(el("p", { class: "done", text: "Follow-up prepared. It is waiting in Approvals." }));
    refreshPending();
  } catch (error) {
    button.disabled = false;
    toast(error.message, "error");
  }
}

async function renderApprovals() {
  const list = el("div", { class: "approvals" }, el("p", { class: "hint", text: "Loading approvals..." }));
  const history = el("div");
  showPage(
    header("Approvals", "Nothing is sent or changed until you approve it. In this demo, approved actions are recorded on this computer instead of being sent."),
    list,
    history,
  );
  let data;
  try {
    data = await api("/api/approvals");
  } catch (error) {
    list.replaceChildren(el("p", { class: "empty", text: error.message }));
    return;
  }
  state.pending = data.pending.length;
  renderNav();
  list.replaceChildren(
    ...(data.pending.length
      ? data.pending.map(approvalView)
      : [el("p", { class: "empty", text: "Nothing is waiting for approval. Prepare follow-ups from Check invoices." })]),
  );
  if (data.history.length) {
    history.replaceChildren(
      el(
        "details",
        { class: "history" },
        el("summary", { text: `History (${data.history.length})` }),
        el(
          "ul",
          {},
          data.history.map((action) =>
            el(
              "li",
              {},
              action.status === "executed" ? stamp("Approved", "ok") : stamp("Rejected", "alert"),
              el("span", { text: `${action.title}. ${action.decided_by}, ${new Date(action.decided_at).toLocaleString()}` }),
            ),
          ),
        ),
      ),
    );
  }
}

function approvalView(action) {
  const preview =
    action.kind === "send_email"
      ? el("pre", { class: "email", text: `To: ${action.payload.to}\nSubject: ${action.payload.subject}\n\n${action.payload.body}` })
      : el(
          "ul",
          { class: "details-list" },
          Object.entries(action.payload)
            .filter(([, value]) => value)
            .map(([key, value]) => el("li", { text: `${key.replace(/_/g, " ").replace(/^./, (letter) => letter.toUpperCase())}: ${value}` })),
        );
  const card = el("article", { class: "item" });
  const approve = el("button", { type: "button", class: "primary", text: "Approve" });
  const reject = el("button", { type: "button", class: "secondary", text: "Reject" });
  approve.onclick = () => decide(action, "approve", card, [approve, reject]);
  reject.onclick = () => decide(action, "reject", card, [approve, reject]);
  card.append(
    stamp("Waiting for approval", "neutral"),
    el("h3", { text: action.title }),
    action.warnings.map((warning) => el("p", { class: "warning", text: warning })),
    preview,
    el("div", { class: "actions" }, approve, reject),
  );
  return card;
}

async function decide(action, decision, card, buttons) {
  buttons.forEach((button) => { button.disabled = true; });
  try {
    await api(`/api/approvals/${encodeURIComponent(action.id)}/${decision}`, { method: "POST" });
    const approved = decision === "approve";
    card.append(stamp(approved ? "Approved" : "Rejected", approved ? "ok" : "alert", "stamp-overlay"));
    toast(approved ? "Approved. Recorded on this computer; nothing was sent." : "Rejected. Nothing was sent.");
    setTimeout(() => { if (state.page === "approvals") renderApprovals(); }, 1100);
  } catch (error) {
    buttons.forEach((button) => { button.disabled = false; });
    toast(error.message, "error");
  }
}

async function renderAdmin() {
  const body = $("#admin-body");
  const parts = [
    el(
      "p",
      {},
      el("strong", { text: `Sent to ${state.provider}: ` }),
      "your question, the document passages used to answer it, and invoices you check.",
    ),
    el("p", {}, el("strong", { text: "Kept on this computer: " }), "the document index, access rules, approvals and the activity log."),
  ];
  body.replaceChildren(...parts);
  if (!state.user.pages.includes("invoices")) return;
  let accounting;
  try {
    accounting = await api("/api/accounting");
  } catch {
    return;
  }
  const status = el(
    "p",
    {},
    el("span", { class: `status-dot${accounting.enabled ? "" : " off"}`, "aria-hidden": "true" }),
    accounting.enabled ? "Accounting connection on" : `Access revoked by ${accounting.changed_by}`,
  );
  const toggle = el("button", {
    type: "button",
    class: "admin-button",
    text: accounting.enabled ? "Revoke access" : "Restore access",
    onclick: async () => {
      toggle.disabled = true;
      try {
        await api("/api/accounting", { method: "POST", body: { enabled: !accounting.enabled } });
        toast(accounting.enabled ? "Access revoked. The accounting connection is off." : "Access restored.");
      } catch (error) {
        toast(error.message, "error");
      }
      renderAdmin();
    },
  });
  const logArea = el("div");
  const logButton = el("button", {
    type: "button",
    class: "admin-button",
    text: "Show activity log",
    onclick: async () => {
      const data = await api("/api/activity").catch(() => ({ rows: [] }));
      logArea.replaceChildren(
        data.rows.length
          ? el(
              "table",
              { class: "activity" },
              el("tbody", {}, data.rows.slice(0, 25).map((row) => el("tr", {}, el("td", { text: row.what }), el("td", { text: row.documents || "-" }), el("td", { text: row.result || "-" })))),
            )
          : el("p", { text: "No activity yet." }),
      );
    },
  });
  body.append(
    el("p", {}, el("strong", { text: "Accounting connection" }), el("br"), accounting.label),
    status,
    toggle,
    logButton,
    logArea,
  );
}

async function start() {
  try {
    const response = await fetch("/api/bootstrap");
    const data = await response.json();
    state.provider = data.provider;
    state.users = data.users;
  } catch {
    $("#main").replaceChildren(el("p", { class: "empty", text: "PrivateDesk could not start. Check that the server is running." }));
    return;
  }
  const saved = readStored(STORAGE_KEY);
  state.user = state.users.find((user) => user.id === saved) || state.users[0];
  renderUsers();
  renderAdmin();
  navigate(window.location.hash.slice(1) || "ask");
  window.addEventListener("hashchange", () => {
    const page = window.location.hash.slice(1);
    if (page !== state.page) navigate(page);
  });
}

document.addEventListener("DOMContentLoaded", start);
