import argparse
from dataclasses import dataclass

from app.config import settings
from app.generation.llm import RateLimiter, RateLimitExceeded, get_chat_model
from app.generation.prompt import NOT_FOUND_MESSAGE, SYSTEM_PROMPT, GroundedAnswer, build_user_input, source_id
from app.retrieval.retriever import RetrievedChunk, Retriever

ANSWERED = "answered"
NO_RELEVANT_PASSAGES = "no_relevant_passages"
MODEL_NOT_FOUND = "model_not_found"
NO_VALID_CITATIONS = "no_valid_citations"


@dataclass(frozen=True)
class RagAnswer:
    question: str
    role: str
    found: bool
    answer: str
    reason: str
    citations: tuple[RetrievedChunk, ...]
    retrieved: tuple[RetrievedChunk, ...]

    @property
    def cited_documents(self):
        return tuple(dict.fromkeys(chunk.document for chunk in self.citations))


def validate_question(question):
    question = (question or "").strip()
    if not question:
        raise ValueError("Please enter a question.")
    if len(question) > settings.max_question_chars:
        raise ValueError(f"The question is too long. Keep it under {settings.max_question_chars} characters.")
    return question


class RagChain:
    def __init__(self, retriever=None, chat_model=None, rate_limiter=None, min_relevance=None, top_k=None, activity_log=None):
        self.retriever = retriever or Retriever()
        self.chat_model = chat_model or get_chat_model()
        self.rate_limiter = rate_limiter or RateLimiter()
        self.min_relevance = settings.min_relevance if min_relevance is None else min_relevance
        self.top_k = top_k or settings.top_k
        self.activity_log = activity_log

    def answer(self, question, role, user="anonymous"):
        question = validate_question(question)
        try:
            self.rate_limiter.check(user)
        except RateLimitExceeded:
            if self.activity_log:
                self.activity_log.record(user, role, "question", question=question, outcome="throttled")
            raise
        result = self._answer(question, role)
        if self.activity_log:
            self.activity_log.log_question(user, role, result)
        return result

    def _answer(self, question, role):
        retrieved = tuple(self.retriever.retrieve(question, role, self.top_k))
        relevant = [chunk for chunk in retrieved if chunk.score >= self.min_relevance]
        if not relevant:
            return self._not_found(question, role, NO_RELEVANT_PASSAGES, retrieved)
        result = self.chat_model.generate_structured(SYSTEM_PROMPT, build_user_input(question, relevant), GroundedAnswer)
        if not result.found:
            return self._not_found(question, role, MODEL_NOT_FOUND, retrieved)
        by_id = {source_id(position): chunk for position, chunk in enumerate(relevant, start=1)}
        cited = tuple(by_id[cited_id] for cited_id in dict.fromkeys(result.citations) if cited_id in by_id)
        if not cited or not result.answer.strip():
            return self._not_found(question, role, NO_VALID_CITATIONS, retrieved)
        return RagAnswer(question, role, True, result.answer.strip(), ANSWERED, cited, retrieved)

    @staticmethod
    def _not_found(question, role, reason, retrieved):
        return RagAnswer(question, role, False, NOT_FOUND_MESSAGE, reason, (), retrieved)


def main():
    parser = argparse.ArgumentParser(description="Ask a question as a given role.")
    parser.add_argument("question")
    parser.add_argument("--role", required=True, choices=settings.roles)
    args = parser.parse_args()
    result = RagChain().answer(args.question, args.role, user="cli")
    print(result.answer)
    for chunk in result.citations:
        print(f"\n[{chunk.citation}]\n{chunk.text}")
    if not result.found:
        print(f"\n(reason: {result.reason})")


if __name__ == "__main__":
    main()
