import json

from app.config import settings
from app.extraction.schemas import FIELD_NAMES

REPORT_DIR = settings.root_dir / "reports"
JSON_PATH = REPORT_DIR / "evaluation_results.json"
MARKDOWN_PATH = REPORT_DIR / "evaluation_results.md"
SECTION_TITLES = {
    "build": "Build set (used while developing)",
    "holdout": "Held-out set (never used while developing)",
}


def load_report():
    if JSON_PATH.exists():
        return json.loads(JSON_PATH.read_text(encoding="utf-8"))
    return {}


def save_report(report):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    MARKDOWN_PATH.write_text(render_markdown(report), encoding="utf-8")
    return JSON_PATH, MARKDOWN_PATH


def cell(value, limit=90):
    text = " ".join(str(value if value is not None else "-").split()).replace("|", "/")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def table(headers, rows, limit=90):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(cell(value, limit) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def render_config(config):
    return table(
        ["Setting", "Value"],
        [
            ("Generated", config["generated_at"]),
            ("Embeddings", f"{config['embedding_provider']} / {config['embedding_model']} ({config['embedding_dimensions']} dims)"),
            ("Answers and extraction", f"{config['chat_provider']} / {config['chat_model']}"),
            ("Chunk size / overlap", f"{config['chunk_size']} / {config['chunk_overlap']}"),
            ("Top k / minimum relevance", f"{config['top_k']} / {config['min_relevance']}"),
            ("Index includes held-out documents", config["includes_holdout"]),
        ],
    )


def render_questions(section):
    summary = section["summary"]
    lines = [
        table(
            ["Measure", "Result"],
            [
                ("Right passage in the top 3 (answerable)", summary["hit_at_3"]),
                ("Right passage at rank 1 (answerable)", summary["hit_at_1"]),
                ("Answered and cited the expected source", summary["answered_correctly"]),
                ("Correctly refused (not found)", summary["refused_correctly"]),
                ("Overall", summary["overall"]),
                ("Role leaks (assigned role / cross-role sweep)", f"{summary['role_leaks']} / {summary['sweep_leaks']}"),
            ],
        ),
        "",
        table(
            ["Id", "Role", "Question", "Expected", "Got", "Rank", "Correct", "Answer or reason"],
            [
                (
                    row["id"], row["role"], row["question"], "answer" if row["answerable"] else "refuse",
                    "answer" if row["found"] else "refuse", row["retrieval_rank"] or "-",
                    "yes" if row["correct"] else "NO", row["answer"] if row["found"] else row["reason"],
                )
                for row in section["questions"]
            ],
        ),
    ]
    return "\n".join(lines)


def render_security(rows):
    return table(
        ["Id", "Risk", "Role", "Passed", "Checks", "Answer"],
        [
            (
                row["id"], row["risk"], row["role"], "yes" if row["passed"] else "NO",
                "; ".join(f"{name}: {'ok' if ok else 'FAIL'}" for name, ok in row["checks"].items()), row["answer"],
            )
            for row in rows
        ],
        limit=240,
    )


def render_invoices(runs):
    lines = [
        table(
            ["Run", "Field accuracy", "Clean invoices fully correct", "Messy invoices flagged, not filled", "Books comparison as expected"],
            [
                (number, run["field_accuracy"], run["clean_correct"], run["messy_flagged"], run["discrepancies_correct"])
                for number, run in enumerate(runs, start=1)
            ],
        ),
        "",
        "Per-field results from run 1 (`*` = must be flagged for review and left empty):",
        "",
        table(
            ["File", *FIELD_NAMES, "Books comparison"],
            [
                (
                    score["file"],
                    *[("ok" if score["fields"][name] else "WRONG") + ("*" if name in score["expected_review"] else "") for name in FIELD_NAMES],
                    next((row["got"] for row in runs[0]["discrepancies"] if row["file"] == score["file"]), "-"),
                )
                for score in runs[0]["scores"]
            ],
        ),
    ]
    return "\n".join(lines)


def render_section(name, section):
    parts = [f"## {SECTION_TITLES[name]}", "", render_config(section["config"]), "", "### Questions", "", render_questions(section)]
    if section.get("security"):
        parts += ["", "### Security cases", "", render_security(section["security"])]
    if section.get("invoices"):
        parts += ["", f"### Invoices ({len(section['invoices'])} runs)", "", render_invoices(section["invoices"])]
    return "\n".join(parts)


def render_markdown(report):
    parts = [
        "# PrivateDesk evaluation results",
        "",
        "Generated by `python -m app.evaluation.evaluator --report` (build set) and "
        "`python -m app.evaluation.evaluator --report --holdout` (held-out set). All data is synthetic.",
    ]
    for name in ("build", "holdout"):
        if name in report:
            parts += ["", render_section(name, report[name])]
    return "\n".join(parts) + "\n"
