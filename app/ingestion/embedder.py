import openai
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from openai import OpenAI

from app.config import settings
from app.utils.helpers import describe_api_error

MISSING_KEY = "{name} is not set. Copy .env.example to .env and add your key."


class EmbeddingError(RuntimeError):
    pass


def build_gemini_client(api_key, error_type):
    if not api_key:
        raise error_type(MISSING_KEY.format(name="GEMINI_API_KEY"))
    return genai.Client(
        api_key=api_key,
        http_options=genai_types.HttpOptions(
            timeout=int(settings.api_timeout_seconds * 1000),
            retry_options=genai_types.HttpRetryOptions(attempts=settings.api_max_retries + 1),
        ),
    )


def check_count(vectors, texts):
    if len(vectors) != len(texts):
        raise EmbeddingError(f"Expected {len(texts)} embeddings, received {len(vectors)}.")
    return vectors


class OpenAIEmbedder:
    provider = "openai"
    dimensions = None

    def __init__(self, model=None, api_key=None, batch_size=None, client=None):
        self.model = model or settings.embedding_model
        self.batch_size = batch_size or settings.embedding_batch_size
        self._client = client or self._build_client(api_key or settings.openai_api_key)

    @staticmethod
    def _build_client(api_key):
        if not api_key:
            raise EmbeddingError(MISSING_KEY.format(name="OPENAI_API_KEY"))
        return OpenAI(api_key=api_key, timeout=settings.api_timeout_seconds, max_retries=settings.api_max_retries)

    def embed_documents(self, texts):
        vectors = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start:start + self.batch_size])
            try:
                response = self._client.embeddings.create(model=self.model, input=batch)
            except openai.OpenAIError as error:
                raise EmbeddingError(f"Embedding request failed: {describe_api_error(error)}") from None
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda item: item.index))
        return check_count(vectors, texts)

    def embed_query(self, text):
        return self.embed_documents([text])[0]


class GeminiEmbedder:
    provider = "gemini"

    def __init__(self, model=None, api_key=None, batch_size=None, dimensions=None, client=None):
        self.model = model or settings.embedding_model
        self.batch_size = batch_size or settings.embedding_batch_size
        self.dimensions = dimensions or settings.embedding_dimensions
        self._client = client or build_gemini_client(api_key or settings.gemini_api_key, EmbeddingError)

    def _embed(self, texts, task_type):
        vectors = []
        for start in range(0, len(texts), self.batch_size):
            contents = [
                genai_types.Content(parts=[genai_types.Part.from_text(text=text)])
                for text in texts[start:start + self.batch_size]
            ]
            config = genai_types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dimensions)
            try:
                response = self._client.models.embed_content(model=self.model, contents=contents, config=config)
            except genai_errors.APIError as error:
                raise EmbeddingError(f"Embedding request failed: {describe_api_error(error)}") from None
            vectors.extend(list(embedding.values) for embedding in response.embeddings)
        return check_count(vectors, texts)

    def embed_documents(self, texts):
        return self._embed(list(texts), "RETRIEVAL_DOCUMENT")

    def embed_query(self, text):
        return self._embed([text], "RETRIEVAL_QUERY")[0]


EMBEDDERS = {"openai": OpenAIEmbedder, "gemini": GeminiEmbedder}


def get_embedder(provider=None):
    provider = provider or settings.embedding_provider
    if provider not in EMBEDDERS:
        raise EmbeddingError(f"Unknown embedding provider '{provider}'.")
    return EMBEDDERS[provider]()
