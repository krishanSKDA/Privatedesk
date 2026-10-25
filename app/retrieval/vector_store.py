from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import settings

ADD_BATCH_SIZE = 500


class VectorStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    text: str
    metadata: dict
    score: float


class VectorStore:
    def __init__(self, path=None, collection_name=None):
        self.path = Path(path or settings.vector_db_dir)
        self.collection_name = collection_name or settings.collection_name
        self._client = chromadb.PersistentClient(
            path=str(self.path),
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False),
        )

    def exists(self):
        return self.collection_name in {collection.name for collection in self._client.list_collections()}

    def _collection(self):
        if not self.exists():
            raise VectorStoreError(f"No index found in {self.path}. Run 'python run_ingestion.py' first.")
        return self._client.get_collection(self.collection_name, embedding_function=None)

    def rebuild(self, chunks, embeddings, index_info):
        if len(chunks) != len(embeddings):
            raise VectorStoreError(f"{len(chunks)} chunks but {len(embeddings)} embeddings.")
        if self.exists():
            self._client.delete_collection(self.collection_name)
        collection = self._client.create_collection(
            self.collection_name,
            configuration={"hnsw": {"space": "cosine"}},
            metadata={key: str(value) for key, value in index_info.items()},
            embedding_function=None,
        )
        for start in range(0, len(chunks), ADD_BATCH_SIZE):
            batch = chunks[start:start + ADD_BATCH_SIZE]
            collection.add(
                ids=[chunk.chunk_id for chunk in batch],
                embeddings=embeddings[start:start + ADD_BATCH_SIZE],
                documents=[chunk.text for chunk in batch],
                metadatas=[chunk.to_metadata() for chunk in batch],
            )
        return collection.count()

    def index_info(self):
        return dict(self._collection().metadata or {})

    def count(self):
        return self._collection().count()

    def query(self, embedding, k, where):
        result = self._collection().query(
            query_embeddings=[embedding],
            n_results=k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        return [
            SearchHit(chunk_id=chunk_id, text=text, metadata=metadata, score=1.0 - distance)
            for chunk_id, text, metadata, distance in zip(
                result["ids"][0], result["documents"][0], result["metadatas"][0], result["distances"][0]
            )
        ]
