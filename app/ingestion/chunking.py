import json
from dataclasses import asdict, dataclass
from pathlib import Path

from app.config import settings
from app.ingestion.loader import LoadedDocument
from app.utils.helpers import relative_to_root

SEPARATORS = ("\n\n", "\n", ". ", "; ", ", ", " ")


class RoleRuleError(ValueError):
    pass


@dataclass(frozen=True)
class DocumentRule:
    document: str
    title: str
    doc_type: str
    roles: tuple[str, ...]
    split: str


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    document: str
    source_path: str
    title: str
    doc_type: str
    page: int | None
    chunk_index: int
    char_start: int
    char_end: int
    roles: tuple[str, ...]
    split: str
    text: str

    @property
    def embedding_text(self):
        return f"{self.title}\n\n{self.text}"

    def to_metadata(self, all_roles=None):
        metadata = {
            "document": self.document,
            "source_path": self.source_path,
            "title": self.title,
            "doc_type": self.doc_type,
            "chunk_index": self.chunk_index,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "split": self.split,
            "roles": ",".join(self.roles),
        }
        if self.page is not None:
            metadata["page"] = self.page
        for role in all_roles or settings.roles:
            metadata[f"role_{role}"] = role in self.roles
        return metadata

    def to_record(self):
        return {**asdict(self), "roles": list(self.roles)}

    @classmethod
    def from_record(cls, record):
        return cls(**{**record, "roles": tuple(record["roles"])})


def load_document_rules(manifest_path: Path = None, valid_roles=None):
    manifest_path = manifest_path or settings.manifest_path
    valid_roles = set(valid_roles or settings.roles)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rules = {}
    for entry in manifest["documents"]:
        name = entry["document"]
        roles = tuple(entry.get("roles") or ())
        if not roles:
            raise RoleRuleError(f"{name}: no roles assigned in the manifest.")
        unknown = set(roles) - valid_roles
        if unknown:
            raise RoleRuleError(f"{name}: unknown roles {sorted(unknown)} in the manifest.")
        if name in rules:
            raise RoleRuleError(f"{name}: listed more than once in the manifest.")
        rules[name] = DocumentRule(name, entry["title"], entry["doc_type"], roles, entry["split"])
    return rules


def skip_whitespace(text, position):
    while position < len(text) and text[position].isspace():
        position += 1
    return position


def find_break(text, start, limit):
    floor = start + (limit - start) // 2
    for separator in SEPARATORS:
        index = text.rfind(separator, floor, limit)
        if index != -1:
            return index + len(separator)
    return limit


def find_overlap_start(text, start, end, overlap):
    if overlap == 0:
        return end
    candidate = max(end - overlap, start + 1)
    for separator in SEPARATORS:
        index = text.find(separator, candidate, end)
        if index != -1:
            return index + len(separator)
    return candidate


def split_spans(text, size, overlap):
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("chunk size must be positive and larger than the overlap.")
    spans = []
    start = skip_whitespace(text, 0)
    while start < len(text):
        end = len(text) if len(text) - start <= size else find_break(text, start, start + size)
        trimmed = end
        while trimmed > start and text[trimmed - 1].isspace():
            trimmed -= 1
        if trimmed > start:
            spans.append((start, trimmed))
        if end >= len(text):
            break
        start = skip_whitespace(text, find_overlap_start(text, start, end, overlap))
    return spans


def chunk_document(document: LoadedDocument, rule: DocumentRule, size=None, overlap=None, root=None):
    size = size or settings.chunk_size
    overlap = settings.chunk_overlap if overlap is None else overlap
    source_path = relative_to_root(document.path, root or settings.root_dir)
    chunks = []
    for page in document.pages:
        for start, end in split_spans(page.text, size, overlap):
            index = len(chunks)
            chunks.append(
                Chunk(
                    chunk_id=f"{rule.document}#{index:03d}",
                    document=rule.document,
                    source_path=source_path,
                    title=rule.title,
                    doc_type=rule.doc_type,
                    page=page.page,
                    chunk_index=index,
                    char_start=start,
                    char_end=end,
                    roles=rule.roles,
                    split=rule.split,
                    text=page.text[start:end],
                )
            )
    return chunks
