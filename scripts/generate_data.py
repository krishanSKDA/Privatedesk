import csv
import json
from pathlib import Path

import pymupdf

from synthetic.company import COMPANY, ROLES
from synthetic.documents import DOCUMENTS
from synthetic.eval_sets import HOLDOUT_QUESTIONS, QUESTIONS, SECURITY_CASES
from synthetic.finance import (
    BANK_ACCOUNT,
    HALCYON_BOOKS_AMOUNT,
    INVOICES,
    SUPPLIERS,
    bank_statement_rows,
    build_books,
    expected_discrepancies,
    invoice_totals,
    invoice_truth,
)
from synthetic.invoice_layouts import INVOICE_CSS, render_invoice_html
from synthetic.render import render_pdf, render_scanned_pdf

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SPLIT_DIRS = {"build": DATA_DIR / "raw", "holdout": DATA_DIR / "holdout"}
BANK_STATEMENT_NAME = "bank_statement_2026-09.csv"
EXTRACT_FLAGS = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES


def relative(path):
    return path.relative_to(ROOT).as_posix()


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_documents():
    entries = []
    for document in DOCUMENTS:
        path = SPLIT_DIRS[document["split"]] / "documents" / document["filename"]
        if document["format"] == "pdf":
            author = document.get("author", COMPANY["name"])
            render_pdf(path, document["pages"], document["title"], author, footer=document["footer"])
            pages = len(document["pages"])
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(document["text"], encoding="utf-8")
            pages = None
        entries.append(
            {
                "document": document["filename"],
                "path": relative(path),
                "title": document["title"],
                "doc_type": document["doc_type"],
                "roles": document["roles"],
                "split": document["split"],
                "format": document["format"],
                "pages": pages,
                "contains_prompt_injection": document.get("contains_prompt_injection", False),
            }
        )
    return entries


def write_invoices():
    entries = []
    for seed, invoice in enumerate(INVOICES, start=1):
        path = SPLIT_DIRS[invoice["split"]] / "invoices" / invoice["filename"]
        supplier = SUPPLIERS[invoice["supplier"]]["name"]
        title = f"Invoice from {supplier}"
        html = render_invoice_html(invoice)
        if invoice["scanned"]:
            render_scanned_pdf(path, html, title, supplier, seed=seed, css=INVOICE_CSS)
        else:
            render_pdf(path, [html], title, supplier, css=INVOICE_CSS)
        entries.append(
            {
                "document": invoice["filename"],
                "path": relative(path),
                "title": title,
                "doc_type": "invoice",
                "roles": ["finance"],
                "split": invoice["split"],
                "format": "pdf",
                "pages": 1,
                "scanned": invoice["scanned"],
                "layout": invoice["layout"],
            }
        )
    return entries


def write_bank_statement():
    path = SPLIT_DIRS["build"] / "statements" / BANK_STATEMENT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = bank_statement_rows()
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return {
        "document": BANK_STATEMENT_NAME,
        "path": relative(path),
        "title": "Bank statement, operating account, 1 Sep to 15 Oct 2026",
        "doc_type": "bank_statement",
        "roles": ["finance"],
        "split": "build",
        "format": "csv",
        "pages": None,
        "account": BANK_ACCOUNT,
    }


def write_outputs():
    documents = write_documents() + write_invoices() + [write_bank_statement()]
    books_path = DATA_DIR / "accounting" / "quickbooks_mock.json"
    write_json(books_path, build_books())
    write_json(DATA_DIR / "eval" / "questions.json", {"questions": QUESTIONS, "security_cases": SECURITY_CASES})
    write_json(DATA_DIR / "eval" / "holdout_questions.json", {"questions": HOLDOUT_QUESTIONS})
    write_json(DATA_DIR / "eval" / "invoice_truth.json", {"invoices": invoice_truth()})
    write_json(DATA_DIR / "eval" / "discrepancy_truth.json", {"discrepancies": expected_discrepancies()})
    manifest = {
        "company": COMPANY["name"],
        "synthetic": True,
        "roles": ROLES,
        "mock_accounting": relative(books_path),
        "documents": documents,
    }
    write_json(DATA_DIR / "manifest.json", manifest)
    return manifest


def normalise(text):
    return " ".join(text.replace(" ", " ").lower().split())


def page_text(entry, page):
    path = ROOT / entry["path"]
    if entry["format"] != "pdf":
        return normalise(path.read_text(encoding="utf-8"))
    with pymupdf.open(path) as doc:
        return normalise(doc[page - 1].get_text(flags=EXTRACT_FLAGS))


def check_sources(case, by_name, expected_split, problems):
    for item in case.get("expected_sources", []):
        entry = by_name.get(item["document"])
        if entry is None:
            problems.append(f"{case['id']}: unknown document {item['document']}")
            continue
        if case["role"] not in entry["roles"]:
            problems.append(f"{case['id']}: role {case['role']} cannot see {item['document']}")
        if entry["split"] != expected_split:
            problems.append(f"{case['id']}: {item['document']} is in split {entry['split']}, expected {expected_split}")
        text = page_text(entry, item["page"])
        for evidence in item["evidence"]:
            if normalise(evidence) not in text:
                problems.append(f"{case['id']}: evidence '{evidence}' not found in {item['document']} page {item['page']}")
    blocked = case.get("blocked_source")
    if blocked and case["role"] in by_name[blocked]["roles"]:
        problems.append(f"{case['id']}: role {case['role']} is not actually blocked from {blocked}")


def check_invoices(by_name, problems):
    for invoice in INVOICES:
        entry = by_name[invoice["filename"]]
        text = page_text(entry, 1)
        if invoice["scanned"]:
            if text:
                problems.append(f"{invoice['filename']}: scanned invoice has a text layer")
            continue
        if invoice["invoice_number"] and normalise(invoice["invoice_number"]) not in text:
            problems.append(f"{invoice['filename']}: invoice number missing from text")
        total = invoice_totals(invoice)["total"]
        candidates = {f"{total:,.2f}", f"{total:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")}
        if not any(candidate in text for candidate in candidates):
            problems.append(f"{invoice['filename']}: total {total} missing from text")
    halcyon = next(invoice for invoice in INVOICES if invoice["id"] == "halcyon_hp55102")
    if invoice_totals(halcyon)["total"] == HALCYON_BOOKS_AMOUNT:
        problems.append("Halcyon books amount must differ from the invoice to create a discrepancy")


def validate(manifest):
    by_name = {entry["document"]: entry for entry in manifest["documents"]}
    problems = []
    for case in QUESTIONS + SECURITY_CASES:
        check_sources(case, by_name, "build", problems)
    for case in HOLDOUT_QUESTIONS:
        check_sources(case, by_name, "holdout", problems)
    check_invoices(by_name, problems)
    if sum(1 for case in QUESTIONS if not case["answerable"]) not in (4, 5):
        problems.append("The main question set should have 4 or 5 unanswerable questions")
    return problems


def print_summary(manifest):
    for split in ("build", "holdout"):
        entries = [entry for entry in manifest["documents"] if entry["split"] == split]
        print(f"\n{split.upper()} ({len(entries)} files)")
        for entry in entries:
            extra = " [scanned]" if entry.get("scanned") else ""
            print(f"  {entry['path']:<58} roles={','.join(entry['roles']):<11}{extra}")
    print(f"\nQuestions: {len(QUESTIONS)} main, {len(SECURITY_CASES)} security, {len(HOLDOUT_QUESTIONS)} holdout")


def main():
    manifest = write_outputs()
    print_summary(manifest)
    problems = validate(manifest)
    if problems:
        print("\nVALIDATION FAILED")
        for problem in problems:
            print(f"  - {problem}")
        raise SystemExit(1)
    print("\nValidation passed: every expected answer appears on its cited page, roles and splits are consistent.")


if __name__ == "__main__":
    main()
