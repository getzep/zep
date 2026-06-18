"""Interactive CLI for the Zep + Claude Opus 4.8 memory agent.

Run `python ingest.py` once first — it seeds the demo user's Zep graph with
two prior conversations, so the agent starts with cross-session memory.

Usage:

    # Cache-preserving mode (default — Opus 4.8 mid-conversation system messages)
    python chat.py

    # Baseline (context block in the system prompt; only the prefix caches)
    python chat.py --context-mode system-prompt

    # Chat as a different Zep user / continue an existing thread
    python chat.py --user-id <id> --thread-id <id>

Each session opens a fresh thread for the demo user. Try asking
"where should I take the team for dinner?" — the agent already knows about
the dietary restrictions from the seeded prior conversations.

After each reply a stats line shows exactly what the turn cost:
cached reads vs. cache writes vs. uncached input tokens, plus latency.
Watch `cache read` grow and stay high in system-message mode; watch it stay
at zero in system-prompt mode.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

from anthropic import Anthropic
from dotenv import load_dotenv
from zep_cloud.client import Zep
from zep_cloud.core.api_error import ApiError

import scenario
from agent import (
    MODE_SYSTEM_MESSAGE,
    MODES,
    TurnMetrics,
    ZepMemory,
    ZepMemoryAgent,
)

DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def stats_line(m: TurnMetrics) -> str:
    cost = m.cost
    return (
        f"{DIM}[cache read {m.cache_read_input_tokens:,} | cache write "
        f"{m.cache_creation_input_tokens:,} | uncached in {m.input_tokens:,} | "
        f"out {m.output_tokens:,} | ${cost.total:.4f} | ttft {m.ttft_s:.2f}s | "
        f"total {m.total_s:.2f}s]{RESET}"
    )


def restore_history(zep: Zep, thread_id: str) -> list[dict]:
    """Rebuild the agent's in-context history from a Zep thread's stored
    messages, so resuming a thread shows Claude the conversation so far.

    Only user/assistant turns are replayed, in order. A trailing user message
    from an interrupted session is dropped so the messages array stays
    well-formed once the next user turn is appended.
    """
    messages = zep.thread.get(thread_id=thread_id).messages or []
    history = [
        {"role": m.role, "content": [{"type": "text", "text": m.content}]}
        for m in messages
        if m.role in ("user", "assistant")
    ]
    if history and history[-1]["role"] == "user":
        history.pop()
    return history


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with a Zep-backed Claude Opus 4.8 agent.")
    parser.add_argument(
        "--context-mode",
        choices=MODES,
        default=MODE_SYSTEM_MESSAGE,
        help=(
            "Where the Zep context block is placed: 'system-prompt' appends it to the "
            "system prompt each turn (only the static prefix can be cached); 'system-message' "
            "appends it as an Opus 4.8 mid-conversation system message (the whole conversation "
            "is cached). Default: system-message."
        ),
    )
    parser.add_argument(
        "--user-id",
        default=scenario.DEMO_USER_ID,
        help=f"Zep user ID to chat as (default: {scenario.DEMO_USER_ID}, seeded by ingest.py).",
    )
    parser.add_argument("--thread-id", help="Existing Zep thread ID to continue (default: start a new thread).")
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--show-context", action="store_true", help="Print the retrieved context block each turn.")
    args = parser.parse_args()

    load_dotenv()
    zep_key, anthropic_key = os.getenv("ZEP_API_KEY"), os.getenv("ANTHROPIC_API_KEY")
    if not zep_key or not anthropic_key:
        sys.exit("Set ZEP_API_KEY and ANTHROPIC_API_KEY in .env first (see .env.example).")

    zep = Zep(api_key=zep_key)
    anthropic_client = Anthropic(api_key=anthropic_key)

    # --- resolve user + thread ----------------------------------------------
    user_id = args.user_id
    try:
        zep.user.get(user_id=user_id)
    except ApiError as e:
        if e.status_code == 404:
            sys.exit(f"Zep user '{user_id}' not found. Run `python ingest.py` first to seed the demo user.")
        raise

    thread_id = args.thread_id
    if thread_id is None:
        thread_id = f"{user_id}-chat-{uuid.uuid4().hex[:6]}"
        zep.thread.create(thread_id=thread_id, user_id=user_id)
    print(f"User {BOLD}{user_id}{RESET} | thread {BOLD}{thread_id}{RESET} | context mode: {BOLD}{args.context_mode}{RESET}")
    print(f"{DIM}Commands: /context (show last retrieved memory), /stats (session totals), /quit{RESET}\n")

    user_name = "Dana" if user_id == scenario.DEMO_USER_ID else "User"
    agent = ZepMemoryAgent(
        anthropic_client=anthropic_client,
        memory=ZepMemory(zep, thread_id, user_name=user_name),
        mode=args.context_mode,
        max_tokens=args.max_tokens,
    )

    # Resuming an existing thread: replay its prior turns into the agent's
    # in-context history so Claude sees the conversation so far. (Zep memory
    # persists across sessions regardless; this restores the literal chat
    # transcript, which lives only in the request's messages array.)
    if args.thread_id is not None:
        resumed = restore_history(zep, thread_id)
        agent.history = resumed
        if resumed:
            print(f"{DIM}Resumed {len(resumed)} prior message(s) from this thread.{RESET}\n")

    session_metrics: list[TurnMetrics] = []

    # --- REPL -------------------------------------------------------------------
    while True:
        try:
            user_text = input(f"{BOLD}You:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_text:
            continue
        if user_text in ("/quit", "/exit"):
            break
        if user_text == "/context":
            print(f"{DIM}{agent.last_context or '(no context retrieved yet)'}{RESET}\n")
            continue
        if user_text == "/stats":
            if not session_metrics:
                print(f"{DIM}(no turns yet){RESET}\n")
                continue
            total_cost = sum(m.cost.total for m in session_metrics)
            reads = sum(m.cache_read_input_tokens for m in session_metrics)
            prompt = sum(m.prompt_tokens for m in session_metrics)
            hit_rate = reads / prompt * 100 if prompt else 0.0
            print(
                f"{DIM}{len(session_metrics)} turn(s) | ${total_cost:.4f} | "
                f"cache hit rate {hit_rate:.1f}% of prompt tokens{RESET}\n"
            )
            continue

        reply, metrics = agent.send(user_text)
        session_metrics.append(metrics)
        print(f"\n{BOLD}Mira:{RESET} {reply}\n")
        if args.show_context:
            print(f"{DIM}--- context block ---\n{agent.last_context or '(empty)'}\n---{RESET}")
        print(stats_line(metrics) + "\n")

    print(f"\nUser: {user_id}  Thread: {thread_id}  (pass --thread-id to resume this conversation)")


if __name__ == "__main__":
    main()
