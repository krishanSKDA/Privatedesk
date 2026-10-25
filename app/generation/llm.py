import base64
import threading
import time
from collections import defaultdict, deque

import openai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from openai import OpenAI

from app.config import settings
from app.ingestion.embedder import MISSING_KEY, build_gemini_client
from app.utils.helpers import describe_api_error


class LLMError(RuntimeError):
    pass


class RateLimitExceeded(RuntimeError):
    def __init__(self, retry_after):
        super().__init__(f"Too many questions. Please wait {max(1, round(retry_after))} seconds and try again.")
        self.retry_after = retry_after


class RateLimiter:
    def __init__(self, limit=None, window_seconds=60.0, clock=time.monotonic):
        self.limit = limit or settings.questions_per_minute
        self.window_seconds = window_seconds
        self.clock = clock
        self._events = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, user):
        now = self.clock()
        with self._lock:
            events = self._events[user]
            while events and now - events[0] >= self.window_seconds:
                events.popleft()
            if len(events) >= self.limit:
                raise RateLimitExceeded(self.window_seconds - (now - events[0]))
            events.append(now)


def text_part(text):
    return {"type": "text", "text": text}


def image_part(data, mime_type):
    return {"type": "image", "data": data, "mime_type": mime_type}


def as_parts(user_input):
    return [text_part(user_input)] if isinstance(user_input, str) else list(user_input)


def has_images(user_input):
    return any(part["type"] == "image" for part in as_parts(user_input))


def to_openai_input(user_input):
    content = []
    for part in as_parts(user_input):
        if part["type"] == "text":
            content.append({"type": "input_text", "text": part["text"]})
        else:
            encoded = base64.b64encode(part["data"]).decode("ascii")
            content.append({"type": "input_image", "image_url": f"data:{part['mime_type']};base64,{encoded}", "detail": "high"})
    return [{"role": "user", "content": content}]


def to_gemini_contents(user_input):
    return [
        genai_types.Part.from_text(text=part["text"])
        if part["type"] == "text"
        else genai_types.Part.from_bytes(data=part["data"], mime_type=part["mime_type"])
        for part in as_parts(user_input)
    ]


class OpenAIChatModel:
    provider = "openai"

    def __init__(self, model=None, api_key=None, max_output_tokens=None, reasoning_effort=None, client=None):
        self.model = model or settings.chat_model
        self.max_output_tokens = max_output_tokens or settings.max_answer_tokens
        self.reasoning_effort = settings.chat_reasoning_effort if reasoning_effort is None else reasoning_effort
        self._client = client or self._build_client(api_key or settings.openai_api_key)

    @staticmethod
    def _build_client(api_key):
        if not api_key:
            raise LLMError(MISSING_KEY.format(name="OPENAI_API_KEY"))
        return OpenAI(api_key=api_key, timeout=settings.api_timeout_seconds, max_retries=settings.api_max_retries)

    def generate_structured(self, instructions, user_input, schema):
        options = {"reasoning": {"effort": self.reasoning_effort}} if self.reasoning_effort else {}
        try:
            response = self._client.responses.parse(
                model=self.model,
                instructions=instructions,
                input=to_openai_input(user_input),
                text_format=schema,
                max_output_tokens=self.max_output_tokens,
                store=False,
                **options,
            )
        except openai.OpenAIError as error:
            raise LLMError(f"Chat request failed: {describe_api_error(error)}") from None
        parsed = response.output_parsed
        if parsed is None:
            details = getattr(response, "incomplete_details", None)
            reason = getattr(details, "reason", None) or getattr(response, "status", "unknown")
            raise LLMError(f"The model returned no usable answer ({reason}).")
        return parsed


class GeminiChatModel:
    provider = "gemini"

    def __init__(self, model=None, api_key=None, max_output_tokens=None, reasoning_effort=None, client=None):
        self.model = model or settings.chat_model
        self.max_output_tokens = max_output_tokens or settings.max_answer_tokens
        self.reasoning_effort = settings.chat_reasoning_effort if reasoning_effort is None else reasoning_effort
        self._client = client or build_gemini_client(api_key or settings.gemini_api_key, LLMError)

    def generate_structured(self, instructions, user_input, schema):
        thinking = genai_types.ThinkingConfig(thinking_level=self.reasoning_effort) if self.reasoning_effort else None
        config = genai_types.GenerateContentConfig(
            system_instruction=instructions,
            response_mime_type="application/json",
            response_schema=schema,
            max_output_tokens=self.max_output_tokens,
            thinking_config=thinking,
            media_resolution="MEDIA_RESOLUTION_HIGH" if has_images(user_input) else None,
        )
        try:
            response = self._client.models.generate_content(
                model=self.model, contents=to_gemini_contents(user_input), config=config
            )
        except genai_errors.APIError as error:
            raise LLMError(f"Chat request failed: {describe_api_error(error)}") from None
        parsed = response.parsed
        if isinstance(parsed, schema):
            return parsed
        raise LLMError(f"The model returned no usable answer ({self._stop_reason(response)}).")

    @staticmethod
    def _stop_reason(response):
        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            return f"blocked: {feedback.block_reason}"
        candidates = getattr(response, "candidates", None) or []
        reason = getattr(candidates[0], "finish_reason", None) if candidates else None
        return str(getattr(reason, "value", reason) or "no candidates")


CHAT_MODELS = {"openai": OpenAIChatModel, "gemini": GeminiChatModel}


def get_chat_model(provider=None, **options):
    provider = provider or settings.chat_provider
    if provider not in CHAT_MODELS:
        raise LLMError(f"Unknown chat provider '{provider}'.")
    return CHAT_MODELS[provider](**options)
