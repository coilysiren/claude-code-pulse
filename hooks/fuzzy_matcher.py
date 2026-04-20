"""Optional fuzzy matcher for summary intent.

Uses `rapidfuzz` (pure-C, ~200KB wheel, no ML model) to catch phrasings the
regex misses without paying the 2-3s sentence-transformers startup cost. Good
middle tier: handles typos, word-order swaps, and light paraphrases cheaply.

Returns False if rapidfuzz is not installed — summary_rollup.py falls through
to the semantic tier (if enabled) or gives up.

Install:
    pip install rapidfuzz
"""
from __future__ import annotations

REFERENCE_PHRASES = [
    "summarize what we did",
    "give me a summary",
    "everything done",
    "wrap things up",
    "final report",
    "total it all up",
    "where are we at",
    "recap the session",
    "how much have we spent",
    "session rundown",
    "status check",
    "tally the tokens",
]


def matches_summary_fuzzy(prompt: str, threshold: int = 75) -> bool:
    """Token-set ratio match against reference phrases. Threshold 0-100."""
    try:
        from rapidfuzz import fuzz, process  # type: ignore
    except ImportError:
        return False
    try:
        best = process.extractOne(
            prompt.lower(),
            REFERENCE_PHRASES,
            scorer=fuzz.token_set_ratio,
        )
        return bool(best and best[1] >= threshold)
    except Exception:
        return False
