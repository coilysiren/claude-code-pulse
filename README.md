# claude-code-pulse

Lightweight session-vitals for Claude Code: a Python statusline and a
UserPromptSubmit hook that surfaces per-turn and session-wide token/cost
metrics from the transcript JSONL.

## What it shows

**Statusline** (after every message):

```
opus-4.7 · git:main · ctx 310k/1M (30%) (+4.2k) · turn in 4.2k out 1.1k · cache 90% hit · $0.4 session (+$0.05)
```

- `git:<branch>` — current git branch of the workspace (omitted outside repos).
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

## Status icons

Each metric is annotated 🟢 / 🟡 / 🔴 based on thresholds calibrated for personal
(not enterprise) use, sourced from ccusage constants + Anthropic caching
guidance + community reporting:

| Metric       | 🟢 green  | 🟡 yellow    | 🔴 red    | Rationale |
|--------------|-----------|--------------|-----------|-----------|
| ctx usage    | <50%      | 50–80%       | >80%      | Claude Code auto-compacts ~80%; above that you're racing it. |
| cache hit    | >80%      | 50–80%       | <50%      | Cache writes cost 1.25x, reads 0.1x → breakeven ~22%; healthy sessions post 85–95%. |
| session cost | <$5       | $5–$20       | >$20      | Max-$100 plan ≈ $140/mo API equiv; >$20 in one session eats the envelope. |
| burn rate    | <$5/hr    | $5–$15/hr    | >$15/hr   | Sonnet coding ≈ $2–4/hr, Opus mixed ≈ $6–12/hr; >$15/hr sustained is Opus-in-a-loop. |

Thresholds live at the top of `rollup_lib.py` — edit them if your normal is
different.

## Install

```bash
git clone https://github.com/coilysiren/claude-code-pulse ~/projects/claude-code-pulse
ln -s ~/projects/claude-code-pulse/statusline.py           ~/.claude/statusline.py
ln -s ~/projects/claude-code-pulse/rollup_lib.py           ~/.claude/rollup_lib.py
mkdir -p ~/.claude/hooks
ln -s ~/projects/claude-code-pulse/hooks/summary_rollup.py ~/.claude/hooks/summary_rollup.py
ln -s ~/projects/claude-code-pulse/hooks/fuzzy_matcher.py  ~/.claude/hooks/fuzzy_matcher.py
ln -s ~/projects/claude-code-pulse/hooks/semantic_matcher.py ~/.claude/hooks/semantic_matcher.py
```

Optional deps (all fall back gracefully if absent):
```bash
pip install -r requirements-optional.txt  # orjson, rapidfuzz, sentence-transformers
# or à la carte:
pip install rapidfuzz                     # recommended — enables Tier 2 matching
pip install orjson                        # recommended — faster JSONL parse
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

## Summary-intent matching (three tiers)

`looks_like_summary()` in `hooks/summary_rollup.py` cascades through tiers,
stopping at the first match:

1. **Regex** (stdlib, instant) — covers `summarize`, `wrap up`, `recap`,
   `rundown`, `tally`, `where are we`, etc.
2. **Fuzzy** (`rapidfuzz`, ~1ms) — token-set ratio against reference phrases;
   catches typos and paraphrases the regex misses. Requires `pip install
   rapidfuzz`. Disable with `CC_PULSE_FUZZY=0`.
3. **Semantic** (`sentence-transformers` + `all-MiniLM-L6-v2`, ~90MB, fully
   local after first download) — catches loose paraphrases that fuzzy misses.
   Opt-in via `CC_PULSE_SEMANTIC=1` because the ~2–3s cold start runs on every
   invocation that reaches this tier.

Short-prompt-only gate: tiers 2 and 3 only run on prompts under 80 chars, and
tier 3 additionally requires a summary signal word, so long task prompts never
pay the cost.

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
