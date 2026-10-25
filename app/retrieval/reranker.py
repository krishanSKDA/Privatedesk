import math
import re
from collections import Counter

from app.config import settings

TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:[.,/-][a-z0-9]+)*")
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from", "get", "has", "have",
    "how", "i", "in", "is", "it", "its", "many", "much", "of", "on", "or", "the", "their", "there", "this", "to",
    "was", "we", "what", "when", "where", "which", "who", "why", "will", "with", "you",
}


def tokenize(text):
    return [token for token in TOKEN_PATTERN.findall(text.lower()) if token not in STOPWORDS]


class LexicalReranker:
    name = "lexical"

    def __init__(self, keyword_weight=None, k1=1.5, b=0.75):
        self.keyword_weight = settings.rerank_keyword_weight if keyword_weight is None else keyword_weight
        self.k1 = k1
        self.b = b

    def keyword_scores(self, question, chunks):
        query = set(tokenize(question))
        documents = [Counter(tokenize(chunk.text)) for chunk in chunks]
        if not query or not documents:
            return [0.0] * len(chunks)
        average_length = sum(sum(document.values()) for document in documents) / len(documents) or 1.0
        frequency = {term: sum(1 for document in documents if term in document) for term in query}
        scores = []
        for document in documents:
            length = sum(document.values())
            score = 0.0
            for term in query:
                count = document.get(term, 0)
                if not count:
                    continue
                idf = math.log(1 + (len(documents) - frequency[term] + 0.5) / (frequency[term] + 0.5))
                score += idf * count * (self.k1 + 1) / (count + self.k1 * (1 - self.b + self.b * length / average_length))
            scores.append(score)
        return scores

    def rerank(self, question, chunks, top_k):
        if not chunks:
            return []
        keyword = self.keyword_scores(question, chunks)
        highest = max(keyword) or 1.0
        combined = [
            (1 - self.keyword_weight) * chunk.score + self.keyword_weight * keyword_score / highest
            for chunk, keyword_score in zip(chunks, keyword)
        ]
        order = sorted(range(len(chunks)), key=lambda index: combined[index], reverse=True)
        return [chunks[index] for index in order[:top_k]]


RERANKERS = {"lexical": LexicalReranker}


def get_reranker(name=None):
    name = (settings.reranker if name is None else name).lower()
    if name in ("", "none", "off"):
        return None
    if name not in RERANKERS:
        raise ValueError(f"Unknown reranker '{name}'. Use one of: none, {', '.join(RERANKERS)}.")
    return RERANKERS[name]()
