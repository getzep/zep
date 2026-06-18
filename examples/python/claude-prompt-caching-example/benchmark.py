"""Benchmark: the same conversation, with the Zep context block in the
system prompt (only the static prefix can be cached) vs. as Opus 4.8
mid-conversation system messages (everything can be cached).

Prerequisite: run ``python ingest.py`` once — it seeds the demo user's Zep
graph with two prior conversations, so the benchmark conversation starts
with real cross-session memory.

Phases:

1. **Capture** — play the scripted conversation into a fresh Zep thread for
   the demo user, capturing the default Zep context block returned on every
   turn (and polling between turns so extraction keeps pace, like a
   real-paced conversation).
2. **Replay × 2** — run the identical conversation through Claude Opus 4.8
   twice: once with context in the system prompt and a breakpoint on the
   static prefix (``system-prompt`` — the best caching can do with that
   placement), once as a trailing mid-conversation system message with full
   caching of everything before it (``system-message``). Both replays use
   the *same captured context blocks*, the *same scripted assistant
   replies*, and the same replace-each-turn context handling, so the
   prompts are token-for-token equivalent — the only variable is where the
   context block sits, which determines whether the conversation history
   can be cached. Each replay gets a unique salt in its system prompt so
   runs cannot hit each other's cache (or a previous run's).
3. **Report** — per-turn and aggregate token, cost, and latency comparison,
   printed and saved to ``results/``.

Usage:

    python benchmark.py --conversation short   # 6 turns
    python benchmark.py --conversation long    # 18 turns

    # Re-run the replays + report without touching Zep (reuses the captured
    # context blocks from a previous run's results file):
    python benchmark.py --reuse results/<file>.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv
from zep_cloud.client import Zep
from zep_cloud.core.api_error import ApiError
from zep_cloud.types import Message

import pricing
import scenario
from agent import (
    MODE_SYSTEM_MESSAGE,
    MODE_SYSTEM_PROMPT,
    ReplayMemory,
    TurnMetrics,
    ZepMemoryAgent,
    wait_for_zep_processing,
)

RESULTS_DIR = Path(__file__).parent / "results"


# ---------------------------------------------------------------------------
# Phase 1: play the conversation into Zep and capture per-turn context blocks
# ---------------------------------------------------------------------------


def capture_contexts(zep: Zep, turns: list[dict], wait_between_turns: bool) -> tuple[str, list[str | None]]:
    user_id = scenario.DEMO_USER_ID
    try:
        zep.user.get(user_id=user_id)
    except ApiError as e:
        if e.status_code == 404:
            sys.exit(f"Zep user '{user_id}' not found. Run `python ingest.py` first to seed the demo user.")
        raise

    thread_id = f"{user_id}-bench-{uuid.uuid4().hex[:6]}"
    zep.thread.create(thread_id=thread_id, user_id=user_id)

    print(f"Capture: playing {len(turns)} turns into thread {thread_id}...")
    contexts: list[str | None] = []
    for i, turn in enumerate(turns, start=1):
        # Persist the user message and get the default Zep context block in
        # the same call (one round trip instead of add + get_user_context).
        response = zep.thread.add_messages(
            thread_id=thread_id,
            messages=[Message(role="user", name="Dana", content=turn["user"])],
            return_context=True,
        )
        contexts.append(response.context)
        zep.thread.add_messages(
            thread_id=thread_id,
            messages=[Message(role="assistant", name="Mira", content=turn["assistant"])],
        )
        print(f"Capture: turn {i}/{len(turns)} — context block {len(response.context or ''):,} chars")
        if wait_between_turns and i < len(turns):
            # Let extraction keep pace, like a real-paced conversation, so
            # later turns retrieve facts established in earlier ones.
            wait_for_zep_processing(zep, user_id, timeout_s=90.0)
    return thread_id, contexts


# ---------------------------------------------------------------------------
# Phase 2: replay through Claude, one mode at a time
# ---------------------------------------------------------------------------


def replay(
    anthropic_client: Anthropic,
    mode: str,
    turns: list[dict],
    contexts: list[str | None],
    max_tokens: int,
) -> list[TurnMetrics]:
    agent = ZepMemoryAgent(
        anthropic_client=anthropic_client,
        memory=ReplayMemory(contexts),
        mode=mode,
        max_tokens=max_tokens,
        system_salt=uuid.uuid4().hex,  # unique prefix → cold cache for this run
    )
    print(f"\nReplay [{mode}]: {len(turns)} turns")
    results = []
    for turn in turns:
        _, m = agent.send(turn["user"], scripted_reply=turn["assistant"])
        results.append(m)
        print(
            f"  turn {m.turn:>2}: read {m.cache_read_input_tokens:>7,} | "
            f"write {m.cache_creation_input_tokens:>6,} | uncached {m.input_tokens:>5,} | "
            f"ttft {m.ttft_s:5.2f}s | ${m.cost.total:.4f}"
        )
    return results


# ---------------------------------------------------------------------------
# Phase 3: report
# ---------------------------------------------------------------------------


def summarize(metrics: list[TurnMetrics]) -> dict:
    reads = sum(m.cache_read_input_tokens for m in metrics)
    writes = sum(m.cache_creation_input_tokens for m in metrics)
    uncached = sum(m.input_tokens for m in metrics)
    output = sum(m.output_tokens for m in metrics)
    prompt = reads + writes + uncached
    input_cost = sum(m.cost.input_total for m in metrics)
    total_cost = sum(m.cost.total for m in metrics)
    nc_cost = sum(
        pricing.no_cache_cost(
            m.input_tokens, m.cache_creation_input_tokens, m.cache_read_input_tokens, m.output_tokens
        )
        for m in metrics
    )
    return {
        "turns": len(metrics),
        "prompt_tokens": prompt,
        "cache_read_tokens": reads,
        "cache_write_tokens": writes,
        "uncached_input_tokens": uncached,
        "output_tokens": output,
        "cache_hit_rate_pct": round(reads / prompt * 100, 1) if prompt else 0.0,
        "input_cost_usd": round(input_cost, 4),
        "total_cost_usd": round(total_cost, 4),
        "no_cache_equivalent_cost_usd": round(nc_cost, 4),
        "ttft_mean_s": round(statistics.mean(m.ttft_s for m in metrics), 2),
        "ttft_median_s": round(statistics.median(m.ttft_s for m in metrics), 2),
        "latency_mean_s": round(statistics.mean(m.total_s for m in metrics), 2),
    }


def print_report(baseline: dict, cached: dict) -> None:
    rows = [
        ("Turns", "turns", "{:,}"),
        ("Prompt tokens processed", "prompt_tokens", "{:,}"),
        ("  served from cache", "cache_read_tokens", "{:,}"),
        ("  written to cache", "cache_write_tokens", "{:,}"),
        ("  uncached", "uncached_input_tokens", "{:,}"),
        ("Cache hit rate", "cache_hit_rate_pct", "{:.1f}%"),
        ("Output tokens", "output_tokens", "{:,}"),
        ("Input cost", "input_cost_usd", "${:.4f}"),
        ("Total cost", "total_cost_usd", "${:.4f}"),
        ("Equivalent cost w/o caching", "no_cache_equivalent_cost_usd", "${:.4f}"),
        ("TTFT (mean)", "ttft_mean_s", "{:.2f}s"),
        ("TTFT (median)", "ttft_median_s", "{:.2f}s"),
        ("Turn latency (mean)", "latency_mean_s", "{:.2f}s"),
    ]
    name_w = max(len(r[0]) for r in rows) + 2
    col_w = 24
    print("\n" + "=" * (name_w + 2 * col_w))
    print(f"{'':<{name_w}}{'system-prompt':>{col_w}}{'system-message':>{col_w}}")
    print(f"{'':<{name_w}}{'(prefix-only caching)':>{col_w}}{'(full caching)':>{col_w}}")
    print("-" * (name_w + 2 * col_w))
    for label, key, fmt in rows:
        print(f"{label:<{name_w}}{fmt.format(baseline[key]):>{col_w}}{fmt.format(cached[key]):>{col_w}}")
    print("=" * (name_w + 2 * col_w))

    def ratio(a: float, b: float) -> str:
        return f"{a / b:.1f}x" if b else "n/a"

    print("\nHeadline numbers:")
    print(
        f"  Input-token cost:  ${baseline['input_cost_usd']:.4f} -> ${cached['input_cost_usd']:.4f}  "
        f"({ratio(baseline['input_cost_usd'], cached['input_cost_usd'])} cheaper)"
    )
    print(
        f"  Total cost:        ${baseline['total_cost_usd']:.4f} -> ${cached['total_cost_usd']:.4f}  "
        f"({ratio(baseline['total_cost_usd'], cached['total_cost_usd'])} cheaper)"
    )
    print(
        f"  TTFT (median):     {baseline['ttft_median_s']:.2f}s -> {cached['ttft_median_s']:.2f}s  "
        f"({ratio(baseline['ttft_median_s'], cached['ttft_median_s'])} faster)"
    )
    print(
        f"  Cache hit rate:    {baseline['cache_hit_rate_pct']:.1f}% -> {cached['cache_hit_rate_pct']:.1f}%"
    )


# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare prefix-only caching (context in system prompt) vs full caching (mid-conversation system messages)."
    )
    parser.add_argument("--conversation", choices=sorted(scenario.CONVERSATIONS), default="short")
    parser.add_argument("--reuse", metavar="RESULTS_JSON", help="Reuse captured contexts from a previous results file (skips Zep seeding).")
    parser.add_argument("--no-wait", action="store_true", help="Skip waiting for Zep extraction between captured turns (faster, thinner context).")
    parser.add_argument("--max-tokens", type=int, default=512)
    args = parser.parse_args()

    load_dotenv()
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if not anthropic_key:
        sys.exit("Set ANTHROPIC_API_KEY in .env first (see .env.example).")
    anthropic_client = Anthropic(api_key=anthropic_key)

    # --- get conversation + per-turn context blocks ---------------------------
    if args.reuse:
        saved = json.loads(Path(args.reuse).read_text())
        conversation_name = saved["conversation"]
        contexts = saved["contexts"]
        bench_thread_id = saved.get("bench_thread_id", "(reused)")
        turns = scenario.CONVERSATIONS[conversation_name]
        print(f"Reusing {len(contexts)} captured context blocks from {args.reuse}")
    else:
        zep_key = os.getenv("ZEP_API_KEY")
        if not zep_key:
            sys.exit("Set ZEP_API_KEY in .env first (see .env.example).")
        conversation_name = args.conversation
        turns = scenario.CONVERSATIONS[conversation_name]
        bench_thread_id, contexts = capture_contexts(Zep(api_key=zep_key), turns, wait_between_turns=not args.no_wait)

    if len(contexts) != len(turns):
        sys.exit(f"Context count ({len(contexts)}) does not match turn count ({len(turns)}).")

    # Persist contexts immediately so a failed replay doesn't waste the seed.
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = RESULTS_DIR / f"{stamp}-{conversation_name}.json"
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": pricing.MODEL,
        "conversation": conversation_name,
        "max_tokens": args.max_tokens,
        "zep_user_id": scenario.DEMO_USER_ID,
        "bench_thread_id": bench_thread_id,
        "contexts": contexts,
    }
    out_path.write_text(json.dumps(payload, indent=2))

    # --- replays ---------------------------------------------------------------
    prefix_chars = len(scenario.STATIC_SYSTEM_PROMPT) + len(json.dumps(scenario.TOOL_DEFINITIONS))
    print(
        f"Static prefix: {prefix_chars:,} chars incl. {len(scenario.TOOL_DEFINITIONS)} tool definitions "
        f"(~12k tokens as measured by the API)"
    )
    t0 = time.monotonic()
    baseline_metrics = replay(anthropic_client, MODE_SYSTEM_PROMPT, turns, contexts, args.max_tokens)
    cached_metrics = replay(anthropic_client, MODE_SYSTEM_MESSAGE, turns, contexts, args.max_tokens)
    print(f"\nReplays finished in {time.monotonic() - t0:.0f}s")

    # --- report ----------------------------------------------------------------
    baseline_summary = summarize(baseline_metrics)
    cached_summary = summarize(cached_metrics)
    print_report(baseline_summary, cached_summary)

    payload["runs"] = {
        MODE_SYSTEM_PROMPT: {"summary": baseline_summary, "turns": [m.as_dict() for m in baseline_metrics]},
        MODE_SYSTEM_MESSAGE: {"summary": cached_summary, "turns": [m.as_dict() for m in cached_metrics]},
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nFull results saved to {out_path}")


if __name__ == "__main__":
    main()
