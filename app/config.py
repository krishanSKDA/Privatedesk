import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]

load_dotenv(ROOT_DIR / ".env")

PROVIDER_MODELS = {
    "gemini": {"embedding": "gemini-embedding-2", "chat": "gemini-3.8-flash", "extraction": "gemini-3.8-flash"},
    "openai": {"embedding": "text-embedding-3-small", "chat": "gpt-5.4-mini", "extraction": "gpt-5.4-mini"},
}


def env_int(name, default):
    return int(os.getenv(name, default))


def env_str(name, default):
    return os.getenv(name, default)


def env_float(name, default):
    return float(os.getenv(name, default))


def provider_model(provider, kind):
    return os.getenv(f"{provider.upper()}_{kind.upper()}_MODEL") or PROVIDER_MODELS[provider][kind]


@dataclass(frozen=True)
class Settings:
    root_dir: Path = ROOT_DIR
    raw_dir: Path = ROOT_DIR / "data" / "raw"
    holdout_dir: Path = ROOT_DIR / "data" / "holdout"
    processed_dir: Path = ROOT_DIR / "data" / "processed"
    manifest_path: Path = ROOT_DIR / "data" / "manifest.json"
    vector_db_dir: Path = ROOT_DIR / "vector_db"
    logs_dir: Path = ROOT_DIR / "logs"
    activity_db_path: Path = ROOT_DIR / "logs" / "privatedesk.sqlite"
    outbox_path: Path = ROOT_DIR / "logs" / "outbox.jsonl"
    accounting_state_path: Path = ROOT_DIR / "logs" / "accounting_connection.json"
    mock_books_path: Path = ROOT_DIR / "data" / "accounting" / "quickbooks_mock.json"
    internal_email_domain: str = field(default_factory=lambda: env_str("INTERNAL_EMAIL_DOMAIN", "meridian-trading.example"))
    roles: tuple[str, ...] = ("finance", "hr")
    allowed_extensions: tuple[str, ...] = (".pdf", ".txt", ".md", ".csv", ".png", ".jpg", ".jpeg")
    max_file_bytes: int = field(default_factory=lambda: env_int("MAX_FILE_MB", 10) * 1024 * 1024)
    max_pdf_pages: int = field(default_factory=lambda: env_int("MAX_PDF_PAGES", 200))
    chunk_size: int = field(default_factory=lambda: env_int("CHUNK_SIZE", 900))
    chunk_overlap: int = field(default_factory=lambda: env_int("CHUNK_OVERLAP", 150))
    ai_provider: str = field(default_factory=lambda: env_str("AI_PROVIDER", "gemini"))
    embedding_provider: str = field(default_factory=lambda: env_str("EMBEDDING_PROVIDER", ""))
    chat_provider: str = field(default_factory=lambda: env_str("CHAT_PROVIDER", ""))
    embedding_dimensions: int = field(default_factory=lambda: env_int("EMBEDDING_DIMENSIONS", 768))
    embedding_batch_size: int = 64
    collection_name: str = "privatedesk_chunks"
    top_k: int = field(default_factory=lambda: env_int("TOP_K", 5))
    reranker: str = field(default_factory=lambda: env_str("RERANKER", "none"))
    rerank_candidates: int = field(default_factory=lambda: env_int("RERANK_CANDIDATES", 20))
    rerank_keyword_weight: float = field(default_factory=lambda: env_float("RERANK_KEYWORD_WEIGHT", 0.3))
    chat_reasoning_effort: str = field(default_factory=lambda: env_str("CHAT_REASONING_EFFORT", "low"))
    max_answer_tokens: int = field(default_factory=lambda: env_int("MAX_ANSWER_TOKENS", 1500))
    min_relevance: float = field(default_factory=lambda: env_float("MIN_RELEVANCE", 0.55))
    max_question_chars: int = 1000
    max_extraction_tokens: int = field(default_factory=lambda: env_int("MAX_EXTRACTION_TOKENS", 8000))
    extraction_reasoning_effort: str = field(default_factory=lambda: env_str("EXTRACTION_REASONING_EFFORT", "medium"))
    extraction_max_pages: int = 3
    extraction_image_dpi: int = 150
    questions_per_minute: int = field(default_factory=lambda: env_int("QUESTIONS_PER_MINUTE", 10))
    api_timeout_seconds: float = 60.0
    api_max_retries: int = 3
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""), repr=False)
    gemini_api_key: str = field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", ""), repr=False
    )

    def __post_init__(self):
        for name in ("embedding_provider", "chat_provider"):
            provider = getattr(self, name) or self.ai_provider
            if provider not in PROVIDER_MODELS:
                raise ValueError(f"Unknown AI provider '{provider}'. Use one of: {', '.join(PROVIDER_MODELS)}.")
            object.__setattr__(self, name, provider)

    @property
    def embedding_model(self):
        return provider_model(self.embedding_provider, "embedding")

    @property
    def chat_model(self):
        return provider_model(self.chat_provider, "chat")

    @property
    def extraction_model(self):
        return provider_model(self.chat_provider, "extraction")


settings = Settings()
