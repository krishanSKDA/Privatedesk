from decimal import Decimal, InvalidOperation

from app.utils.helpers import normalise_supplier


def field_matches(name, actual, expected):
    if actual is None or expected is None:
        return actual == expected
    if name == "supplier":
        return normalise_supplier(actual) == normalise_supplier(expected)
    if name == "amount":
        try:
            return Decimal(actual) == Decimal(expected)
        except InvalidOperation:
            return False
    return actual.strip().upper() == expected.strip().upper()


def field_correct(name, field, expected, expect_review):
    if expect_review:
        return field.status == "needs_review" and field.value is None
    return field.status == "ok" and field_matches(name, field.value, expected)


def normalise(text):
    return " ".join(text.replace(" ", " ").lower().split())


def source_matches(chunk, source):
    if chunk.document != source["document"]:
        return False
    if source.get("page") is not None and chunk.page != source["page"]:
        return False
    text = normalise(chunk.text)
    return all(normalise(evidence) in text for evidence in source.get("evidence", []))


def first_hit_rank(chunks, sources):
    for rank, chunk in enumerate(chunks, start=1):
        if any(source_matches(chunk, source) for source in sources):
            return rank
    return None


def hit_rate(ranks, k):
    if not ranks:
        return 0.0
    return sum(1 for rank in ranks if rank is not None and rank <= k) / len(ranks)


def mean_reciprocal_rank(ranks):
    if not ranks:
        return 0.0
    return sum(1 / rank for rank in ranks if rank) / len(ranks)


def role_leaks(chunks, role):
    return [chunk.chunk_id for chunk in chunks if role not in chunk.roles]
