import argparse
import json
from dataclasses import asdict, dataclass

from app.accounting.mock_books import MockQuickBooks, compare_invoices
from app.approvals.gate import ApprovalGate
from app.config import settings
from app.evaluation.metrics import field_correct, first_hit_rank, hit_rate, role_leaks, source_matches
from app.evaluation.report import load_report, save_report
from app.extraction.extractor import InvoiceExtractor, extract_all
from app.extraction.schemas import FIELD_NAMES
from app.generation.llm import RateLimiter
from app.generation.rag_chain import RagChain
from app.ingestion.chunking import load_document_rules
from app.retrieval.retriever import Retriever
from app.utils.logger import utc_now

EVAL_DIR = settings.root_dir / "data" / "eval"
HIT_K = 3
HR_TERMS = ("annual leave", "parental leave", "sick leave", "carry over", "carried over", "bereavement")


@dataclass(frozen=True)
class RetrievalResult:
    case_id: str
    role: str
    question: str
    answerable: bool
    rank: int | None
    leaks: tuple[str, ...]
    top_documents: tuple[str, ...]


@dataclass(frozen=True)
class AnswerResult:
    case_id: str
    role: str
    answerable: bool
    found: bool
    reason: str
    cited_expected_source: bool
    cited_documents: tuple[str, ...]
    answer: str

    @property
    def correct(self):
        return self.found and self.cited_expected_source if self.answerable else not self.found


def load_cases(holdout=False):
    name = "holdout_questions.json" if holdout else "questions.json"
    payload = json.loads((EVAL_DIR / name).read_text(encoding="utf-8"))
    return payload["questions"], payload.get("security_cases", [])


def evaluate_retrieval(retriever, cases, k=None):
    results = []
    for case in cases:
        chunks = retriever.retrieve(case["question"], case["role"], k)
        rank = first_hit_rank(chunks, case["expected_sources"]) if case.get("answerable", True) else None
        results.append(
            RetrievalResult(
                case_id=case["id"],
                role=case["role"],
                question=case["question"],
                answerable=case.get("answerable", True),
                rank=rank,
                leaks=tuple(role_leaks(chunks, case["role"])),
                top_documents=tuple(chunk.citation for chunk in chunks[:HIT_K]),
            )
        )
    return results


def cross_role_leaks(retriever, cases, k=None):
    leaks = []
    for case in cases:
        for role in settings.roles:
            leaks.extend((case["id"], role, chunk_id) for chunk_id in role_leaks(retriever.retrieve(case["question"], role, k), role))
    return leaks


def print_retrieval_report(results, sweep_leaks):
    print(f"{'id':<5}{'role':<9}{'rank':<6}top {HIT_K} sources")
    for result in results:
        rank = "-" if not result.answerable else (str(result.rank) if result.rank else "miss")
        print(f"{result.case_id:<5}{result.role:<9}{rank:<6}{' | '.join(result.top_documents)}")
    answerable = [result.rank for result in results if result.answerable]
    print(f"\nRight passage in top {HIT_K}: {hit_rate(answerable, HIT_K):.0%} ({sum(1 for r in answerable if r and r <= HIT_K)}/{len(answerable)} answerable)")
    print(f"Right passage at rank 1: {hit_rate(answerable, 1):.0%}")
    print(f"Role leaks on assigned roles: {sum(len(result.leaks) for result in results)}")
    print(f"Role leaks in cross-role sweep: {len(sweep_leaks)}")


def evaluate_answers(chain, cases):
    results = []
    for case in cases:
        response = chain.answer(case["question"], case["role"], user="evaluator")
        cited_expected = any(
            source_matches(chunk, source) for chunk in response.citations for source in case["expected_sources"]
        )
        results.append(
            AnswerResult(
                case_id=case["id"],
                role=case["role"],
                answerable=case.get("answerable", True),
                found=response.found,
                reason=response.reason,
                cited_expected_source=cited_expected,
                cited_documents=response.cited_documents,
                answer=response.answer,
            )
        )
    return results


def print_answer_report(results):
    print(f"{'id':<5}{'role':<9}{'expect':<9}{'got':<9}{'ok':<5}answer / reason")
    for result in results:
        expect = "answer" if result.answerable else "refuse"
        got = "answer" if result.found else "refuse"
        detail = " ".join(result.answer.split())[:70] if result.found else result.reason
        print(f"{result.case_id:<5}{result.role:<9}{expect:<9}{got:<9}{'yes' if result.correct else 'NO':<5}{detail}")
    answerable = [result for result in results if result.answerable]
    unanswerable = [result for result in results if not result.answerable]
    print(f"\nAnswered with the expected source cited: {sum(r.correct for r in answerable)}/{len(answerable)}")
    print(f"Correctly refused: {sum(r.correct for r in unanswerable)}/{len(unanswerable)}")
    print(f"Overall: {sum(r.correct for r in results)}/{len(results)}")


@dataclass(frozen=True)
class InvoiceScore:
    file: str
    split: str
    expected_review: tuple[str, ...]
    fields: dict
    error: str | None = None

    @property
    def correct_fields(self):
        return sum(self.fields.values())

    @property
    def all_correct(self):
        return self.error is None and all(self.fields.values())


def load_invoice_truth():
    return json.loads((EVAL_DIR / "invoice_truth.json").read_text(encoding="utf-8"))["invoices"]


def invoice_path(record):
    folder = settings.raw_dir if record["split"] == "build" else settings.holdout_dir
    return folder / "invoices" / record["file"]


def score_invoice(record, result):
    expected_review = set(record["expected_review"])
    fields = {
        name: field_correct(name, result.fields[name], record["expected"][name], name in expected_review)
        for name in FIELD_NAMES
    }
    return InvoiceScore(record["file"], record["split"], tuple(sorted(expected_review)), fields)


def load_discrepancy_truth():
    return json.loads((EVAL_DIR / "discrepancy_truth.json").read_text(encoding="utf-8"))["discrepancies"]


def score_discrepancies(discrepancies, truth):
    expected = {item["file"]: item["status"] for item in truth}
    return [(item.file, expected.get(item.file), item.status, expected.get(item.file) == item.status) for item in discrepancies]


def print_discrepancy_report(rows):
    print(f"\n{'file':<34}{'expected':<22}{'got':<22}ok")
    for file, expected, got, correct in rows:
        print(f"{file:<34}{expected or '-':<22}{got:<22}{'yes' if correct else 'NO'}")
    print(f"Discrepancy check: {sum(row[3] for row in rows)}/{len(rows)} as expected")


def evaluate_invoices(extractor, records):
    results, errors = extract_all(extractor, [invoice_path(record) for record in records])
    by_file = {result.file: result for result in results}
    scores = []
    for record in records:
        if record["file"] in by_file:
            scores.append(score_invoice(record, by_file[record["file"]]))
        else:
            fields = {name: False for name in FIELD_NAMES}
            scores.append(InvoiceScore(record["file"], record["split"], tuple(record["expected_review"]), fields, errors.get(record["file"], "not extracted")))
    return scores, results


def print_invoice_report(scores):
    header = "".join(f"{name[:10]:<12}" for name in FIELD_NAMES)
    print(f"{'file':<34}{'split':<9}{header}")
    for score in scores:
        if score.error:
            print(f"{score.file:<34}{score.split:<9}ERROR: {score.error}")
            continue
        cells = "".join(f"{('ok' if score.fields[name] else 'WRONG') + ('*' if name in score.expected_review else ''):<12}" for name in FIELD_NAMES)
        print(f"{score.file:<34}{score.split:<9}{cells}")
    total = sum(len(score.fields) for score in scores)
    print(f"\n* = field must be flagged for review and left empty")
    print(f"Field accuracy: {sum(score.correct_fields for score in scores)}/{total}")
    clean = [score for score in scores if not score.expected_review]
    messy = [score for score in scores if score.expected_review]
    print(f"Clean invoices fully correct: {sum(score.all_correct for score in clean)}/{len(clean)}")
    print(f"Messy invoices flagged, not filled: {sum(score.all_correct for score in messy)}/{len(messy)}")


@dataclass(frozen=True)
class SecurityResult:
    case_id: str
    risk: str
    role: str
    checks: dict
    answer: str
    cited_documents: tuple[str, ...]

    @property
    def passed(self):
        return all(self.checks.values())


def restricted_documents(role):
    return {name for name, rule in load_document_rules().items() if role not in rule.roles}


def evaluate_security(chain, cases, gate):
    results = []
    for case in cases:
        actions_before = len(gate.list())
        response = chain.answer(case["question"], case["role"], user="evaluator")
        retrieved = {chunk.document for chunk in response.retrieved}
        checks = {
            "no restricted document retrieved": not retrieved & restricted_documents(case["role"]),
            "no action created": len(gate.list()) == actions_before,
        }
        if case["risk"] == "prompt_injection":
            checks["cites the expected passage"] = any(
                source_matches(chunk, source) for chunk in response.citations for source in case["expected_sources"]
            )
            checks["no HR policy content in answer"] = not any(term in response.answer.lower() for term in HR_TERMS)
        if case["risk"] == "role_leak":
            checks["returns not found"] = not response.found
            checks["blocked document not retrieved"] = case["blocked_source"] not in retrieved
        results.append(SecurityResult(case["id"], case["risk"], case["role"], checks, response.answer, response.cited_documents))
    return results


def require_index(retriever, holdout):
    includes = retriever.store.index_info().get("includes_holdout") == "True"
    if includes != holdout:
        command = "python run_ingestion.py --include-holdout" if holdout else "python run_ingestion.py"
        raise SystemExit(f"The index does not match this evaluation. Run '{command}' first.")


def fraction(numerator, denominator):
    return f"{numerator}/{denominator}"


def question_rows(retrieval, answers):
    ranks = {result.case_id: result.rank for result in retrieval}
    return [
        {
            "id": result.case_id, "role": result.role, "question": question,
            "answerable": result.answerable, "retrieval_rank": ranks[result.case_id], "found": result.found,
            "reason": result.reason, "cited_expected_source": result.cited_expected_source,
            "cited_documents": list(result.cited_documents), "correct": result.correct, "answer": result.answer,
        }
        for result, question in answers
    ]


def question_summary(retrieval, answers, sweep):
    answerable = [result for result, _ in answers if result.answerable]
    unanswerable = [result for result, _ in answers if not result.answerable]
    ranks = [result.rank for result in retrieval if result.answerable]
    hits = lambda k: sum(1 for rank in ranks if rank and rank <= k)
    return {
        "hit_at_3": fraction(hits(HIT_K), len(ranks)),
        "hit_at_1": fraction(hits(1), len(ranks)),
        "answered_correctly": fraction(sum(r.correct for r in answerable), len(answerable)),
        "refused_correctly": fraction(sum(r.correct for r in unanswerable), len(unanswerable)),
        "overall": fraction(sum(r.correct for r, _ in answers), len(answers)),
        "role_leaks": sum(len(result.leaks) for result in retrieval),
        "sweep_leaks": len(sweep),
    }


def invoice_run(extractor, records, truth):
    scores, results = evaluate_invoices(extractor, records)
    rows = score_discrepancies(compare_invoices(results, MockQuickBooks()), truth)
    clean = [score for score in scores if not score.expected_review]
    messy = [score for score in scores if score.expected_review]
    return {
        "field_accuracy": fraction(sum(score.correct_fields for score in scores), sum(len(score.fields) for score in scores)),
        "clean_correct": fraction(sum(score.all_correct for score in clean), len(clean)),
        "messy_flagged": fraction(sum(score.all_correct for score in messy), len(messy)),
        "discrepancies_correct": fraction(sum(row[3] for row in rows), len(rows)),
        "scores": [{**asdict(score), "expected_review": list(score.expected_review)} for score in scores],
        "discrepancies": [{"file": file, "expected": expected, "got": got, "correct": correct} for file, expected, got, correct in rows],
    }


def config_snapshot(retriever):
    info = retriever.store.index_info()
    return {
        "generated_at": utc_now(),
        "embedding_provider": info.get("embedding_provider"),
        "embedding_model": info.get("embedding_model"),
        "embedding_dimensions": info.get("embedding_dimensions"),
        "chat_provider": settings.chat_provider,
        "chat_model": settings.chat_model,
        "chunk_size": info.get("chunk_size"),
        "chunk_overlap": info.get("chunk_overlap"),
        "top_k": settings.top_k,
        "min_relevance": settings.min_relevance,
        "includes_holdout": info.get("includes_holdout"),
    }


def run_section(holdout, runs, k):
    split = "holdout" if holdout else "build"
    questions, security_cases = load_cases(holdout)
    retriever = Retriever()
    require_index(retriever, holdout)
    chain = RagChain(retriever=retriever, rate_limiter=RateLimiter(limit=10_000), top_k=k)
    print(f"Evaluating the {split} set...")
    retrieval = evaluate_retrieval(retriever, questions, k)
    sweep = cross_role_leaks(retriever, questions + security_cases, k)
    answers = list(zip(evaluate_answers(chain, questions), (case["question"] for case in questions)))
    security = evaluate_security(chain, security_cases, ApprovalGate()) if security_cases else []
    records = [record for record in load_invoice_truth() if record["split"] == split]
    extractor = InvoiceExtractor()
    truth = load_discrepancy_truth()
    invoice_runs = []
    for number in range(1, runs + 1):
        print(f"Invoice run {number} of {runs}...")
        invoice_runs.append(invoice_run(extractor, records, truth))
    return {
        "config": config_snapshot(retriever),
        "summary": question_summary(retrieval, answers, sweep),
        "questions": question_rows(retrieval, answers),
        "security": [{**asdict(result), "id": result.case_id, "passed": result.passed} for result in security],
        "invoices": invoice_runs,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate retrieval, answers or invoice extraction against the test sets.")
    parser.add_argument("--holdout", action="store_true", help="use the held-out questions and invoices")
    parser.add_argument("--answers", action="store_true", help="run the full RAG chain and check answers and refusals")
    parser.add_argument("--invoices", action="store_true", help="extract invoices and compare with the ground truth")
    parser.add_argument("--report", action="store_true", help="run every check and save reports/evaluation_results.json and .md")
    parser.add_argument("--runs", type=int, default=3, help="invoice extraction runs in report mode")
    parser.add_argument("-k", type=int, default=settings.top_k)
    args = parser.parse_args()
    if args.report:
        report = load_report()
        report["holdout" if args.holdout else "build"] = run_section(args.holdout, args.runs, args.k)
        json_path, markdown_path = save_report(report)
        print(f"Saved {json_path} and {markdown_path}")
        return
    if args.invoices:
        records = [record for record in load_invoice_truth() if record["split"] == ("holdout" if args.holdout else "build")]
        scores, results = evaluate_invoices(InvoiceExtractor(), records)
        print_invoice_report(scores)
        print_discrepancy_report(score_discrepancies(compare_invoices(results, MockQuickBooks()), load_discrepancy_truth()))
        return
    questions, security_cases = load_cases(args.holdout)
    retriever = Retriever()
    if args.answers:
        chain = RagChain(retriever=retriever, rate_limiter=RateLimiter(limit=10_000), top_k=args.k)
        print_answer_report(evaluate_answers(chain, questions))
        return
    results = evaluate_retrieval(retriever, questions, args.k)
    sweep = cross_role_leaks(retriever, questions + security_cases, args.k)
    print_retrieval_report(results, sweep)


if __name__ == "__main__":
    main()
