#!/usr/bin/env python3
"""UserPromptSubmit hook.

When the user asks for a summary/rollup, inject session-wide stats as
additionalContext so the assistant's reply has ground-truth numbers.

Matching strategy (in order):
  1. Regex fast-path — covers the common phrasings, zero-latency.
  2. Optional semantic fallback — sentence-transformers, only runs when the
     prompt is short AND contains a summary signal word AND regex missed.
     Controlled by env var CC_PULSE_SEMANTIC=1 (off by default because first
     call loads a model ~2-3s).
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".claude"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rollup_lib import (  # noqa: E402
    CONTEXT_WINDOWS, THRESH_BURN_USD_HR, THRESH_CACHE_PCT, THRESH_COST_USD,
    THRESH_CTX_PCT, fmt_cost, fmt_pct, fmt_tokens, context_used,
    icon_hi_bad, icon_hi_good, parse_transcript, session_duration_seconds,
    total_cost, turn_cost,
)

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

SIGNAL_WORDS = {
    "done", "summary", "summarize", "summarise", "status", "total",
    "spent", "cost", "recap", "report", "tally", "progress", "finished",
    "accomplished", "overview", "rundown", "stats", "results",
}


def _has_signal_word(prompt: str) -> bool:
    words = set(re.findall(r"\b\w+\b", prompt.lower()))
    return bool(words & SIGNAL_WORDS)


def looks_like_summary(prompt: str) -> bool:
    if not prompt or len(prompt) > 500:
        # Long prompts are real tasks, not summary requests.
        return False
    # Tier 1: regex (stdlib, instant).
    if any(p.search(prompt) for p in COMPILED):
        return True
    # Only fall through to fuzzy/semantic for short prompts.
    if len(prompt) >= 80:
        return False
    # Tier 2: fuzzy (rapidfuzz, ~1ms). On by default if installed.
    if os.environ.get("CC_PULSE_FUZZY", "1") != "0":
        try:
            from fuzzy_matcher import matches_summary_fuzzy  # noqa: E402
            if matches_summary_fuzzy(prompt):
                return True
        except Exception:
            pass
    # Tier 3: semantic (sentence-transformers, ~2-3s cold start). Opt-in,
    # and gated on a signal word to keep cold-starts rare.
    if (
        os.environ.get("CC_PULSE_SEMANTIC") == "1"
        and _has_signal_word(prompt)
    ):
        try:
            from semantic_matcher import matches_summary_semantic  # noqa: E402
            return matches_summary_semantic(prompt)
        except Exception:
            return False
    return False


RED = "\U0001F534"

# Static remediation notes shown when a signal goes red. URL-verified
# 2026-04 against code.claude.com / platform.claude.com.
REMEDIATION = {
    "ctx": (
        "Context is past the 80% line — Claude Code auto-compacts soon and "
        "instruction-following degrades above that. Use /compact with focus "
        "instructions, /clear between unrelated tasks, move long CLAUDE.md "
        "rules into on-demand skills, and delegate verbose operations to "
        "subagents.\n"
        "  Docs: https://code.claude.com/docs/en/costs#reduce-token-usage\n"
        "  Docs: https://platform.claude.com/docs/en/docs/build-with-claude/context-windows"
    ),
    "cache": (
        "Cache hit rate is under 50% — the prompt prefix is churning, so you "
        "are paying the 1.25x cache-write penalty without amortizing it. "
        "Stabilize CLAUDE.md, tool definitions, and system prompt; anything "
        "that changes early in the prompt invalidates every cached block "
        "after it.\n"
        "  Docs: https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching"
    ),
    "cost": (
        "Session cost is above $20 — on a Max-$100 plan that's a meaningful "
        "slice of the monthly envelope. Consider /model to switch to Sonnet "
        "for routine edits, plan mode before big implementations, and "
        "clearing stale context between tasks.\n"
        "  Docs: https://code.claude.com/docs/en/costs"
    ),
    "burn": (
        "Burn rate is above $15/hr — usually Opus in a tight agentic loop. "
        "Downshift to Sonnet with /model, lower extended-thinking budget "
        "with /effort, or break work into smaller iterations that can "
        "checkpoint.\n"
        "  Docs: https://code.claude.com/docs/en/costs#choose-the-right-model"
    ),
}


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.0f} min"
    hours = minutes / 60
    return f"{hours:.1f} hr"


def build_rollup(transcript_path: str, current_model_id: str) -> str:
    turns = parse_transcript(transcript_path)
    if not turns:
        return "Session rollup: no assistant turns recorded yet."

    tot_in = sum(t["input"] for t in turns)
    tot_cw = sum(t["cache_create"] for t in turns)
    tot_cr = sum(t["cache_read"] for t in turns)
    tot_out = sum(t["output"] for t in turns)
    tot_all_in = tot_in + tot_cw + tot_cr
    cache_frac = (tot_cr / tot_all_in) if tot_all_in else 0.0
    cost = total_cost(turns)

    peak = max(context_used(t) for t in turns)
    last = turns[-1]
    last_ctx = context_used(last)
    ctx_max = (CONTEXT_WINDOWS.get(last.get("model") or "")
               or CONTEXT_WINDOWS.get(current_model_id, 200_000))

    duration_s = session_duration_seconds(turns)
    burn_rate = (cost / (duration_s / 3600.0)) if duration_s >= 60 else None

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

    ranked = sorted(
        ((i + 1, turn_cost(t)) for i, t in enumerate(turns)),
        key=lambda x: x[1],
        reverse=True,
    )[:3]
    top_line = ", ".join(f"#{i} ({fmt_cost(c)})" for i, c in ranked if c > 0)

    cache_icon = icon_hi_good(cache_frac * 100, *THRESH_CACHE_PCT)
    peak_frac = (peak / ctx_max) if ctx_max else 0.0
    last_frac = (last_ctx / ctx_max) if ctx_max else 0.0
    peak_icon = icon_hi_bad(peak_frac * 100, *THRESH_CTX_PCT)
    cur_icon  = icon_hi_bad(last_frac * 100, *THRESH_CTX_PCT)
    cost_icon = icon_hi_bad(cost, *THRESH_COST_USD)
    burn_icon = icon_hi_bad(burn_rate, *THRESH_BURN_USD_HR) if burn_rate else ""

    lines = [
        "[Session rollup — ground-truth from transcript]",
        f"Turns: {len(turns)} assistant responses"
        + (f" over {_fmt_duration(duration_s)}" if duration_s else ""),
        f"Tokens total: {fmt_tokens(tot_all_in + tot_out)}"
        f"  (input {fmt_tokens(tot_in)} / cache-read {fmt_tokens(tot_cr)}"
        f" / cache-write {fmt_tokens(tot_cw)} / output {fmt_tokens(tot_out)})",
        f"{cache_icon} Cache hit rate overall: {fmt_pct(cache_frac)}",
        f"{peak_icon} Peak context: {fmt_tokens(peak)}/{fmt_tokens(ctx_max)}"
        f" ({fmt_pct(peak_frac)})",
        f"{cur_icon} Current context: {fmt_tokens(last_ctx)}/{fmt_tokens(ctx_max)}"
        f" ({fmt_pct(last_frac)})",
        f"{cost_icon} Session cost: {fmt_cost(cost)}"
        + (f"  {burn_icon} burn rate {fmt_cost(burn_rate)}/hr" if burn_rate else ""),
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

    # Append remediation notes for any red signals, deduped in a stable order.
    red_keys: list[str] = []
    if peak_icon == RED or cur_icon == RED:
        red_keys.append("ctx")
    if cache_icon == RED:
        red_keys.append("cache")
    if cost_icon == RED:
        red_keys.append("cost")
    if burn_icon == RED:
        red_keys.append("burn")
    if red_keys:
        lines.append("")
        lines.append("Red-signal remediation:")
        for k in red_keys:
            lines.append(f"{RED} {REMEDIATION[k]}")

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
