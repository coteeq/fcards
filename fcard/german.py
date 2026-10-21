"""Conservative German hints for the canonical back form; not linguistic parsing."""

import re

WORD_TYPES = ("adjective", "verb", "noun", "phrase")
GENDERS = {"masculine": "der", "feminine": "die", "neuter": "das"}
GENDER_LABELS = {key: f"{article} ({key})" for key, article in GENDERS.items()}
INFER = object()
ARTICLE = re.compile(r"^(der|die|das)\s+(\S.*)$", re.IGNORECASE | re.DOTALL)


def split_article(text):
    text = text.strip()
    match = ARTICLE.match(text)
    return (match[1].lower(), match[2]) if match else (None, text)


def infer_type(text):
    article, word = split_article(text)
    if len(word.split()) > 1:
        return "phrase"
    if article:
        return "noun"
    if word.casefold().endswith("en"):
        return "verb"
    return None


def classification(text, word_type=INFER, gender=INFER):
    """Omitted new values are inferred; explicit empty values mean unknown.

    Provenance is intentionally absent: selected and inferred values persist identically.
    """
    if word_type is INFER:
        word_type = infer_type(text)
    if word_type not in (None, "", *WORD_TYPES):
        raise ValueError("Unknown word type.")
    if gender is INFER:
        article, _ = split_article(text)
        gender = next((key for key, value in GENDERS.items() if value == article), None)
    if gender not in (None, "", *GENDERS):
        raise ValueError("Unknown gender.")
    word_type = word_type or None
    return word_type, (gender or None) if word_type == "noun" else None
