"""Anti-loop detection: catch the LLM repeating itself in a tool/reasoning loop.

The recent best-practice note recommends a multi-level approach (raw identity +
semantic cosine + RAG-on-run-history + meta-observer). For the MVP we ship
levels 1 and 2 only:

- raw identity match on stripped content,
- normalised-prefix match on the first 200 characters.

A cosine-similarity layer can be layered on top later once an embedding provider
is wired (see `providers/`).
"""

from __future__ import annotations


import re

_PUNCT = re.compile(r"[^\w\s]")


def _tokens(text: str) -> list[str]:
    return [t for t in _PUNCT.sub(" ", text).lower().split() if t]


def is_loop(history: list[str], candidate: str, *, min_overlap_tokens: int = 3) -> bool:
    """Return True when ``candidate`` looks like a repetition of a past output.

    Compares the leading word sequence (after lower-casing and stripping
    punctuation) of ``candidate`` against each past entry. If either side is
    a prefix of the other for at least ``min_overlap_tokens`` words, that's
    a loop.
    """
    cand_tokens = _tokens(candidate)
    if not cand_tokens:
        return False
    for past in history:
        past_tokens = _tokens(past)
        if not past_tokens:
            continue
        if past_tokens == cand_tokens:
            return True
        head = min(len(past_tokens), len(cand_tokens))
        if head < min_overlap_tokens:
            continue
        if past_tokens[:head] == cand_tokens[:head]:
            return True
    return False
