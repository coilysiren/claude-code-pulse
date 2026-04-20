"""Shared parsing, pricing, and formatting for statusline + rollup hook.

Rounding rule: "1/10th of natural unit, 2 sig figs when >0".
  - percents   -> nearest 10%   (83% -> 80%)
  - dollars    -> nearest $0.10 (0.55 -> 0.6)
  - tokens <1k -> nearest 10    (423 -> 420)
  - tokens     -> 2 sig figs in k/M (4237 -> 4.2k, 312000 -> 310k, 1_050_000 -> 1.1M)
"""
import json
from pathlib import Path

# orjson is 2-10x faster than stdlib json for parsing transcript JSONL.
# Optional — stdlib fallback is fine for sessions under a few thousand turns.
try:
    import orjson  # type: ignore
    _loads = orjson.loads  # type: ignore[assignment]
except ImportError:
    _loads = json.loads  # type: ignore[assignment]

PRICING = {
    "claude-opus-4-7":       {"in": 15.0, "out": 75.0, "cw": 18.75, "cr": 1.5},
    "claude-opus-4-7[1m]":   {"in": 15.0, "out": 75.0, "cw": 18.75, "cr": 1.5},
    "claude-opus-4-6":       {"in": 15.0, "out": 75.0, "cw": 18.75, "cr": 1.5},
    "claude-sonnet-4-6":     {"in": 3.0,  "out": 15.0, "cw": 3.75,  "cr": 0.3},
    "claude-sonnet-4-5":     {"in": 3.0,  "out": 15.0, "cw": 3.75,  "cr": 0.3},
    "claude-haiku-4-5":      {"in": 1.0,  "out": 5.0,  "cw": 1.25,  "cr": 0.1},
}
DEFAULT_PRICE = PRICING["claude-opus-4-7"]

CONTEXT_WINDOWS = {
    "claude-opus-4-7[1m]": 1_000_000,
    "claude-opus-4-7":     200_000,
    "claude-opus-4-6":     200_000,
    "claude-sonnet-4-6":   1_000_000,
    "claude-sonnet-4-5":   1_000_000,
    "claude-haiku-4-5":    200_000,
}


def fmt_tokens(n: float) -> str:
    n = int(round(n))
    if n == 0:
        return "0"
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000:
        v = n / 1_000_000
        s = f"{v:.1f}M" if v < 10 else f"{round(v, -1 if v >= 100 else 0):.0f}M"
    elif n >= 1_000:
        v = n / 1_000
        if v >= 100:
            s = f"{round(v, -1):.0f}k"
        elif v >= 10:
            s = f"{v:.0f}k"
        else:
            s = f"{v:.1f}k"
    else:
        s = f"{round(n, -1):.0f}" if n >= 100 else str(n)
    return sign + s


# Status thresholds for personal/hobby use, sourced from community consensus
# (ccusage constants + Anthropic caching guidance + Reddit/HN reporting).
# See README for rationale. Higher = worse unless noted.
THRESH_CTX_PCT      = (50, 80)   # yellow at 50%, red at 80% (auto-compact zone)
THRESH_CACHE_PCT    = (80, 50)   # hi_good: yellow below 80%, red below 50%
THRESH_COST_USD     = (5, 20)    # $ per session
THRESH_BURN_USD_HR  = (5, 15)    # $ per hour sustained


def icon_hi_bad(value: float, yellow_at: float, red_at: float) -> str:
    """Higher value = worse. yellow_at < red_at."""
    if value >= red_at:
        return "\U0001F534"  # red
    if value >= yellow_at:
        return "\U0001F7E1"  # yellow
    return "\U0001F7E2"      # green


def icon_hi_good(value: float, yellow_below: float, red_below: float) -> str:
    """Higher value = better. red_below < yellow_below."""
    if value < red_below:
        return "\U0001F534"
    if value < yellow_below:
        return "\U0001F7E1"
    return "\U0001F7E2"


def fmt_pct(frac_or_pct: float, already_pct: bool = False) -> str:
    pct = frac_or_pct if already_pct else frac_or_pct * 100
    return f"{int(round(pct / 10.0) * 10)}%"


def fmt_cost(d: float) -> str:
    # nearest $0.10
    r = round(d * 10) / 10
    return f"${r:.1f}"


def fmt_cost_diff(delta: float) -> str:
    if abs(delta) < 0.05:
        return ""
    sign = "+" if delta > 0 else "-"
    return f" ({sign}${abs(delta):.2f})"


def fmt_token_diff(delta: int) -> str:
    if delta == 0:
        return ""
    sign = "+" if delta > 0 else "-"
    return f" ({sign}{fmt_tokens(abs(delta))})"


def parse_transcript(path: str) -> list[dict]:
    """Return list of per-assistant-message usage dicts in file order."""
    turns = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    obj = _loads(line)
                except Exception:
                    continue
                if obj.get("type") != "assistant":
                    continue
                msg = obj.get("message") or {}
                usage = msg.get("usage")
                if not usage:
                    continue
                turns.append({
                    "ts": obj.get("timestamp"),
                    "model": msg.get("model"),
                    "input": usage.get("input_tokens", 0) or 0,
                    "cache_create": usage.get("cache_creation_input_tokens", 0) or 0,
                    "cache_read": usage.get("cache_read_input_tokens", 0) or 0,
                    "output": usage.get("output_tokens", 0) or 0,
                })
    except Exception:
        pass
    return turns


def turn_cost(t: dict) -> float:
    price = PRICING.get(t.get("model") or "", DEFAULT_PRICE)
    return (
        t["input"]        * price["in"]  / 1e6 +
        t["cache_create"] * price["cw"]  / 1e6 +
        t["cache_read"]   * price["cr"]  / 1e6 +
        t["output"]       * price["out"] / 1e6
    )


def total_cost(turns: list[dict]) -> float:
    return sum(turn_cost(t) for t in turns)


def context_used(turn: dict) -> int:
    """Tokens currently occupying context = all input-side tokens on this turn."""
    return turn["input"] + turn["cache_create"] + turn["cache_read"]


def session_duration_seconds(turns: list[dict]) -> float:
    """Return seconds between first and last assistant-turn timestamp, or 0."""
    from datetime import datetime
    ts: list[str] = [s for t in turns if (s := t.get("ts"))]
    if len(ts) < 2:
        return 0.0
    try:
        parsed = [datetime.fromisoformat(s.replace("Z", "+00:00")) for s in ts]
        return (max(parsed) - min(parsed)).total_seconds()
    except Exception:
        return 0.0


def git_branch(cwd: str) -> str | None:
    """Fast git-branch lookup without subprocess. Walks up to find .git/HEAD."""
    if not cwd:
        return None
    p = Path(cwd)
    for _ in range(20):
        head = p / ".git" / "HEAD"
        if head.is_file():
            try:
                content = head.read_text().strip()
            except Exception:
                return None
            if content.startswith("ref: refs/heads/"):
                return content[len("ref: refs/heads/"):]
            return content[:7]  # detached HEAD -> short SHA
        if p == p.parent:
            return None
        p = p.parent
    return None


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def save_state(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    except Exception:
        pass
