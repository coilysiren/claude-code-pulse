# claude-code-pulse

Lightweight session-vitals for Claude Code: a Python statusline and a
UserPromptSubmit hook that surfaces per-turn and session-wide token/cost
metrics from the transcript JSONL.

## What it shows

**Statusline** (after every message):

```
opus-4.7 · ctx 310k/1M (30%) (+4.2k) · turn in 4.2k out 1.1k · cache 90% hit · $0.4 session (+$0.05)
```

- `ctx` — current input-side tokens vs. model's context window, with delta since
  last statusline tick.
- `turn in / out` — tokens on the most recent turn (delta, not cumulative).
- `cache` — fraction of this turn's input served from prompt cache.
- `session` — running cost estimate for this session, with per-turn delta.

**Rollup hook** — when your message matches a summary-intent pattern
(`summarize`, `wrap up`, `where are we`, `final stats`, etc.), the hook injects
a ground-truth session rollup (total tokens, cache hit rate overall, peak ctx,
top-3 expensive turns, per-model breakdown) as additional context so the
assistant's reply uses real numbers instead of guessing.

## Rounding

- percents → nearest 10%
- dollars → nearest $0.10
- tokens → 2 sig figs in k/M

## Install

```bash
git clone https://github.com/coilysiren/claude-code-pulse ~/projects/claude-code-pulse
ln -s ~/projects/claude-code-pulse/statusline.py        ~/.claude/statusline.py
ln -s ~/projects/claude-code-pulse/rollup_lib.py        ~/.claude/rollup_lib.py
mkdir -p ~/.claude/hooks
ln -s ~/projects/claude-code-pulse/hooks/summary_rollup.py ~/.claude/hooks/summary_rollup.py
```

Then add to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 ~/.claude/statusline.py"
  },
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          { "type": "command", "command": "python3 ~/.claude/hooks/summary_rollup.py" }
        ]
      }
    ]
  }
}
```

Restart Claude Code. No dependencies — pure stdlib Python 3.10+.

## Files

- `rollup_lib.py` — shared transcript parsing, pricing, formatting.
- `statusline.py` — reads stdin JSON, prints statusline, persists per-session state
  to `~/.claude/statusline-state.json` so deltas work across invocations.
- `hooks/summary_rollup.py` — UserPromptSubmit hook; regex-matches summary
  intent, emits rollup as `additionalContext`.

## Pricing

Hardcoded in `rollup_lib.PRICING` (per million tokens). Update if Anthropic's
published rates change. Session cost is approximate; it is not a billing number.

## Upgrading the summary matcher

`looks_like_summary()` in `hooks/summary_rollup.py` is regex-only for speed
(the hook runs on every prompt). If you want semantic matching for phrasings
the regex misses, the intended upgrade path is a fully-local embedding model
via `sentence-transformers` (`all-MiniLM-L6-v2` is ~90MB, no network after
first download). Keep the regex as a short-circuit so common phrasings stay
zero-latency.

## Related work

This repo was scoped after surveying existing tools:

- [ccusage](https://github.com/ryoppippi/ccusage) — post-hoc CLI cost/usage
  reporter. Much broader scope.
- [CCometixLine](https://github.com/Haleclipse/CCometixLine) — Rust statusline
  with ctx and cost. No rollup hook.
- [claude-statusline](https://github.com/kamranahmedse/claude-statusline) — JS
  statusline, lighter feature set.
- [claude-code-hooks-multi-agent-observability](https://github.com/disler/claude-code-hooks-multi-agent-observability)
  — hook-based event telemetry to a dashboard.

The distinct angle here is the combination of (1) per-invocation deltas in the
statusline and (2) pattern-triggered rollup injection back into the prompt
flow, so asking "where are we?" gets answered with transcript-derived numbers.

## License

MIT
