"""Optional semantic matcher for summary intent.

Uses `sentence-transformers` (~90MB all-MiniLM-L6-v2 model, pure-local after
first download). This is an OPTIONAL dependency — if it isn't installed, the
function silently returns False and the regex fast-path in summary_rollup.py
handles everything.

Startup cost: ~2-3s to import + load the model. Because the hook is a
short-lived subprocess, the model is loaded fresh on every semantic check. To
keep end-user latency bounded, summary_rollup.py only invokes this matcher
when (a) regex missed AND (b) the prompt is short AND (c) it contains at
least one summary-ish signal word — so semantic runs on maybe 1% of prompts.

Install:
    pip install -r requirements-semantic.txt
"""
from __future__ import annotations

REFERENCE_PHRASES = [
    "summarize what we did",
    "give me a summary of the session",
    "are we all done",
    "wrap things up",
    "final report please",
    "total everything up",
    "where are we at right now",
    "recap the session",
    "what did we accomplish today",
    "how much have we spent so far",
    "rundown of the work",
    "tally the tokens",
    "status check on progress",
    "session overview",
    "stats on this session",
]


def matches_summary_semantic(prompt: str, threshold: float = 0.55) -> bool:
    """Return True if `prompt` is semantically close to any reference phrase.

    Returns False if sentence-transformers is not installed.
    """
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return False

    try:
        model = SentenceTransformer("all-MiniLM-L6-v2")
        ref = model.encode(REFERENCE_PHRASES, normalize_embeddings=True)
        emb = model.encode([prompt], normalize_embeddings=True)
        sims = np.asarray(emb) @ np.asarray(ref).T
        return bool(sims.max() >= threshold)
    except Exception:
        return False
