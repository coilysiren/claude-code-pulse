#!/usr/bin/env python3
"""Claude Code status line: ctx / turn tokens / cache hit / session cost, with deltas."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".claude"))
from rollup_lib import (  # noqa: E402
    CONTEXT_WINDOWS, THRESH_CACHE_PCT, THRESH_COST_USD, THRESH_CTX_PCT,
    fmt_cost, fmt_cost_diff, fmt_pct, fmt_token_diff, fmt_tokens,
    context_used, git_branch, icon_hi_bad, icon_hi_good, load_state,
    parse_transcript, save_state, total_cost,
)

STATE_FILE = Path.home() / ".claude" / "statusline-state.json"


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        print("statusline: bad stdin")
        return

    session_id = data.get("session_id", "unknown")
    transcript_path = data.get("transcript_path", "")
    model = data.get("model") or {}
    model_id = model.get("id", "claude-opus-4-7")
    model_display = model.get("display_name") or model_id
    cwd = (data.get("workspace") or {}).get("current_dir", "")
    branch = git_branch(cwd)

    turns = parse_transcript(transcript_path)
    if not turns:
        print(f"{model_display} \u00b7 no usage yet")
        return

    last = turns[-1]
    ctx = context_used(last)
    ctx_max = CONTEXT_WINDOWS.get(last.get("model") or model_id) \
        or CONTEXT_WINDOWS.get(model_id, 200_000)
    ctx_frac = ctx / ctx_max if ctx_max else 0

    turn_in = ctx
    turn_out = last["output"]

    denom = last["input"] + last["cache_create"] + last["cache_read"]
    cache_frac = (last["cache_read"] / denom) if denom else 0.0

    cost = total_cost(turns)

    state = load_state(STATE_FILE)
    prev = state.get(session_id) or {}
    ctx_delta = ctx - (prev.get("ctx") or 0)
    cost_delta = cost - (prev.get("cost") or 0.0)
    state[session_id] = {"ctx": ctx, "cost": cost}
    save_state(STATE_FILE, state)

    ctx_icon   = icon_hi_bad(ctx_frac * 100, *THRESH_CTX_PCT)
    cache_icon = icon_hi_good(cache_frac * 100, *THRESH_CACHE_PCT)
    cost_icon  = icon_hi_bad(cost, *THRESH_COST_USD)

    parts = [model_display]
    if branch:
        parts.append(f"git:{branch}")
    parts.extend([
        f"{ctx_icon} ctx {fmt_tokens(ctx)}/{fmt_tokens(ctx_max)} ({fmt_pct(ctx_frac)}){fmt_token_diff(ctx_delta)}",
        f"turn in {fmt_tokens(turn_in)} out {fmt_tokens(turn_out)}",
        f"{cache_icon} cache {fmt_pct(cache_frac)} hit",
        f"{cost_icon} {fmt_cost(cost)} session{fmt_cost_diff(cost_delta)}",
    ])
    print(" \u00b7 ".join(parts))


if __name__ == "__main__":
    main()
