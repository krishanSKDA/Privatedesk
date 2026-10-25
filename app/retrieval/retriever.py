import argparse
from dataclasses import dataclass
from functools import lru_cache

from app.config import settings
from app.ingestion.embedder import get_embedder
from app.retrieval.reranker import get_reranker
from app.retrieval.vector_store import VectorStore, VectorStoreError

FROM_SETTINGS = object()


class RoleError(ValueError):
    pass


class RoleLeakError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    document: str
    title: str
    source_path: str
    page: int | None
    char_start: int
    char_end: int
    roles: tuple[str, ...]
    text: str
    score: float

    @classmethod
    def from_hit(cls, hit):
        metadata = hit.metadata
        return cls(
            chunk_id=hit.chunk_id,
            document=metadata["document"],
            title=metadata["title"],
            source_path=metadata["source_path"],
            page=metadata.get("page"),
            char_start=metadata["char_start"],
            char_end=metadata["char_end"],
            roles=tuple(role for role in metadata["roles"].split(",") if role),
            text=hit.text,
            score=hit.score,
        )

    @property
    def citation(self):
        return f"{self.document}, page {self.page}" if self.page else self.document


def role_filter(role):
    if role not in settings.roles:
        raise RoleError(f"Unknown role '{role}'. Valid roles: {', '.join(settings.roles)}.")
    return {f"role_{role}": True}


class Retriever:
    def __init__(self, embedder=None, store=None, top_k=None, reranker=FROM_SETTINGS):
        self.embedder = embedder or get_embedder()
        self.store = store or VectorStore()
        self.top_k = top_k or settings.top_k
        self.reranker = get_reranker() if reranker is FROM_SETTINGS else reranker
        self._embed_query = lru_cache(maxsize=256)(self.embedder.embed_query)
        self._check_index_matches_embedder()

    def _check_index_matches_embedder(self):
        info = self.store.index_info()
        expected = {
            "embedding_provider": self.embedder.provider,
            "embedding_model": self.embedder.model,
            "embedding_dimensions": str(getattr(self.embedder, "dimensions", None)),
        }
        actual = {key: info.get(key) for key in expected}
        if actual != expected:
            raise VectorStoreError(
                f"The index was built with {actual} but the retriever uses {expected}. Re-run 'python run_ingestion.py'."
            )

    def retrieve(self, question, role, k=None):
        where = role_filter(role)
        question = question.strip()
        if not question:
            raise ValueError("The question is empty.")
        k = k or self.top_k
        fetch = max(k, settings.rerank_candidates) if self.reranker else k
        hits = self.store.query(self._embed_query(question), fetch, where)
        results = [RetrievedChunk.from_hit(hit) for hit in hits]
        leaked = [result.chunk_id for result in results if role not in result.roles]
        if leaked:
            raise RoleLeakError(f"Role '{role}' received chunks it may not see: {leaked}")
        if self.reranker:
            return self.reranker.rerank(question, results, k)
        return results


def main():
    parser = argparse.ArgumentParser(description="Search the index as a given role.")
    parser.add_argument("question")
    parser.add_argument("--role", required=True, choices=settings.roles)
    parser.add_argument("-k", type=int, default=settings.top_k)
    args = parser.parse_args()
    for rank, chunk in enumerate(Retriever().retrieve(args.question, args.role, args.k), start=1):
        preview = " ".join(chunk.text.split())[:160]
        print(f"{rank}. [{chunk.score:.3f}] {chunk.citation}\n   {preview}")


if __name__ == "__main__":
    main()
