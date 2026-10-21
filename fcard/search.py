"""English/German word search; deliberately separate from answer grading."""

from functools import lru_cache
import re
import unicodedata

NON_ASCII_WORD = re.compile(r"[^a-z0-9]")
UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"})


def search_keys(text):
    """Keep full ASCII spelling and a second key treating ae/oe/ue as a/o/u.

    Keeping both keys means plain English/German substrings remain searchable even
    when a literal digraph is not an umlaut (e.g. 'ell' in 'Duell').
    NFC also handles German umlauts entered as a vowel plus combining diaeresis.
    """
    expanded = unicodedata.normalize("NFC", text).casefold().translate(UMLAUTS)
    expanded = NON_ASCII_WORD.sub("", expanded)
    folded = expanded.replace("ae", "a").replace("oe", "o").replace("ue", "u")
    return expanded, folded


@lru_cache(maxsize=128)
def query_keys(query):
    return search_keys(query)


def matches_search(text, query):
    """SQLite scalar function: literal normalized substring, never SQL wildcards."""
    needles = query_keys(query)
    if not needles[0]:
        return 0
    haystacks = search_keys(text)
    return int(any(
        needle in haystack
        for needle in needles
        for haystack in haystacks
    ))
