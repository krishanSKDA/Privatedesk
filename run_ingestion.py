import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import settings
from app.ingestion.chunking import chunk_document, load_document_rules
from app.ingestion.embedder import EmbeddingError, get_embedder
from app.ingestion.loader import FileRejectedError, load_document
from app.retrieval.vector_store import VectorStore

CHUNKS_FILE = "chunks.jsonl"


@dataclass
class IngestionReport:
    indexed: dict[str, int] = field(default_factory=dict)
    needs_ocr: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    rejected: dict[str, str] = field(default_factory=dict)


def default_roots(include_holdout=False):
    return [settings.raw_dir] + ([settings.holdout_dir] if include_holdout else [])


def discover_files(roots):
    return sorted(path for root in roots if root.exists() for path in root.rglob("*") if path.is_file())


def ingest(include_holdout=False, manifest_path=None, roots=None):
    rules = load_document_rules(manifest_path)
    report = IngestionReport()
    chunks = []
    for path in discover_files(roots or default_roots(include_holdout)):
        rule = rules.get(path.name)
        if rule is None:
            report.skipped[path.name] = "no role rule in the manifest, so no role may see it"
            continue
        try:
            document = load_document(path)
        except FileRejectedError as error:
            report.rejected[path.name] = str(error)
            continue
        if document.needs_ocr:
            report.needs_ocr.append(path.name)
            continue
        document_chunks = chunk_document(document, rule)
        if not document_chunks:
            report.skipped[path.name] = "no extractable text"
            continue
        chunks.extend(document_chunks)
        report.indexed[path.name] = len(document_chunks)
    return chunks, report


def write_chunks(chunks, path=None):
    path = path or settings.processed_dir / CHUNKS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk.to_record(), ensure_ascii=False) + "\n")
    return path


def build_index(chunks, embedder=None, store=None, include_holdout=False):
    embedder = embedder or get_embedder()
    store = store or VectorStore()
    embeddings = embedder.embed_documents([chunk.embedding_text for chunk in chunks])
    index_info = {
        "embedding_provider": embedder.provider,
        "embedding_model": embedder.model,
        "embedding_dimensions": getattr(embedder, "dimensions", None),
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "includes_holdout": include_holdout,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return store.rebuild(chunks, embeddings, index_info)


def print_report(report, chunks, output_path):
    print(f"Indexed {len(report.indexed)} files into {len(chunks)} chunks:")
    for name, count in report.indexed.items():
        print(f"  {name:<45} {count:>3} chunks")
    if report.needs_ocr:
        print("\nScanned with no text layer (left for the invoice extractor):")
        for name in report.needs_ocr:
            print(f"  {name}")
    if report.skipped:
        print("\nSkipped:")
        for name, reason in report.skipped.items():
            print(f"  {name}: {reason}")
    if report.rejected:
        print("\nRejected:")
        for reason in report.rejected.values():
            print(f"  {reason}")
    print(f"\nChunks written to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Load, chunk, embed and index the documents in data/raw.")
    parser.add_argument("--include-holdout", action="store_true", help="also ingest data/holdout (final evaluation only)")
    parser.add_argument("--skip-index", action="store_true", help="only write chunks, do not call the embedding API")
    args = parser.parse_args()
    chunks, report = ingest(include_holdout=args.include_holdout)
    output_path = write_chunks(chunks)
    print_report(report, chunks, output_path)
    if args.skip_index:
        return
    try:
        count = build_index(chunks, include_holdout=args.include_holdout)
    except EmbeddingError as error:
        raise SystemExit(f"\nIndexing stopped: {error}")
    print(f"Indexed {count} chunks in {settings.vector_db_dir} with {settings.embedding_provider}/{settings.embedding_model}")


if __name__ == "__main__":
    main()
