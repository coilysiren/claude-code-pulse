#!/usr/bin/env python3
"""UserPromptSubmit hook.

When the user asks for a summary/rollup, inject session-wide stats as
additionalContext so the assistant's reply has ground-truth numbers.

Matching strategy: lightweight regex/keyword patterns. Fast enough to run on
every prompt. If you want semantic matching later, swap `looks_like_summary`
to call a local model (e.g. ollama with a small embedding model) — keep the
regex as a fast-path short-circuit.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".claude"))
from rollup_lib import (  # noqa: E402
    CONTEXT_WINDOWS, fmt_cost, fmt_pct, fmt_tokens, context_used,
    parse_transcript, total_cost, turn_cost,
)

# Ordered from most specific to most generic.
SUMMARY_PATTERNS = [
    r"\b(?:all|every(?:thing)?)\s+done\??\s*(?:summari[sz]e)?",
    r"\bsummari[sz]e\b",
    r"\bsummary\b",
    r"\broll\s*up\b|\brollup\b",
    r"\bwrap(?:\s*-?\s*up| it up| things up)\b",
    r"\bwhere\s+(?:are|did)\s+we\b",
    r"\bstatus\s+report\b",
    r"\btotal(?:s)?\s+(?:it|them|everything|this)?\s*up\b",
    r"\bfinal\s+(?:stats|numbers|tally|report|rundown)\b",
    r"\bhow\s+(?:much|many)\s+(?:tokens?|did\s+(?:we|this))\b",
    r"\brundown\b",
    r"\brecap\b",
    r"\btally\b",
]
COMPILED = [re.compile(p, re.I) for p in SUMMARY_PATTERNS]


def looks_like_summary(prompt: str) -> bool:
    if not prompt or len(prompt) > 500:
        # Don't fire on long prompts - user is giving a real task, not asking
        # for a summary.
        return False
    return any(p.search(prompt) for p in COMPILED)


def build_rollup(transcript_path: str, current_model_id: str) -> str:
    turns = parse_transcript(transcript_path)
    if not turns:
        return "Session rollup: no assistant turns recorded yet."

    # Aggregate totals
    tot_in = sum(t["input"] for t in turns)
    tot_cw = sum(t["cache_create"] for t in turns)
    tot_cr = sum(t["cache_read"] for t in turns)
    tot_out = sum(t["output"] for t in turns)
    tot_all_in = tot_in + tot_cw + tot_cr
    cache_frac = (tot_cr / tot_all_in) if tot_all_in else 0.0
    cost = total_cost(turns)

    # Peak context
    peak = max(context_used(t) for t in turns)
    last = turns[-1]
    last_ctx = context_used(last)
    ctx_max = (CONTEXT_WINDOWS.get(last.get("model") or "")
               or CONTEXT_WINDOWS.get(current_model_id, 200_000))

    # Model breakdown
    by_model: dict[str, dict] = {}
    for t in turns:
        m = t.get("model") or "unknown"
        d = by_model.setdefault(m, {"turns": 0, "in": 0, "cw": 0, "cr": 0, "out": 0, "cost": 0.0})
        d["turns"] += 1
        d["in"] += t["input"]
        d["cw"] += t["cache_create"]
        d["cr"] += t["cache_read"]
        d["out"] += t["output"]
        d["cost"] += turn_cost(t)

    # Top 3 most expensive turns (1-indexed)
    ranked = sorted(
        ((i + 1, turn_cost(t)) for i, t in enumerate(turns)),
        key=lambda x: x[1],
        reverse=True,
    )[:3]
    top_line = ", ".join(f"#{i} ({fmt_cost(c)})" for i, c in ranked if c > 0)

    lines = [
        "[Session rollup — ground-truth from transcript]",
        f"Turns: {len(turns)} assistant responses",
        f"Tokens total: {fmt_tokens(tot_all_in + tot_out)}"
        f"  (input {fmt_tokens(tot_in)} / cache-read {fmt_tokens(tot_cr)}"
        f" / cache-write {fmt_tokens(tot_cw)} / output {fmt_tokens(tot_out)})",
        f"Cache hit rate overall: {fmt_pct(cache_frac)}",
        f"Peak context: {fmt_tokens(peak)}/{fmt_tokens(ctx_max)}"
        f" ({fmt_pct(peak / ctx_max if ctx_max else 0)})",
        f"Current context: {fmt_tokens(last_ctx)}/{fmt_tokens(ctx_max)}"
        f" ({fmt_pct(last_ctx / ctx_max if ctx_max else 0)})",
        f"Session cost: {fmt_cost(cost)}",
    ]
    if len(by_model) > 1:
        lines.append("By model:")
        for m, d in sorted(by_model.items(), key=lambda kv: -kv[1]["cost"]):
            lines.append(
                f"  {m}: {d['turns']} turns, {fmt_cost(d['cost'])}, "
                f"out {fmt_tokens(d['out'])}"
            )
    if top_line:
        lines.append(f"Top 3 expensive turns: {top_line}")
    lines.append(
        "Use these numbers when answering; do not re-estimate from memory."
    )
    return "\n".join(lines)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    prompt = data.get("prompt", "") or ""
    if not looks_like_summary(prompt):
        sys.exit(0)

    transcript = data.get("transcript_path", "") or ""
    model_id = (data.get("model") or {}).get("id", "") or ""
    rollup = build_rollup(transcript, model_id)

    out = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": rollup,
        }
    }
    print(json.dumps(out))


if __name__ == "__main__":
    main()
