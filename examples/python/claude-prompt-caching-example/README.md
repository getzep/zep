# Zep + Claude Opus 4.8: Cache-Preserving Memory Injection

> **Memory security:** Both modes pass retrieved Zep context through user messages. The mode names remain for compatibility with this benchmark. Retrieved context is never placed in the system prompt.

This example measures prompt caching with a static system prompt and two placements for retrieved context data.

This example wires [Zep](https://www.getzep.com/) into that pattern and measures the difference.

## The two setups

**`system-prompt` (the baseline — prefix-only caching).** The Zep context block is appended to the current user message. A breakpoint on the static system block caches the persona and tools. The conversation history is not cached in this mode:

| Role | Content | Billed at |
|---|---|---|
| System | Static prompt + tools (never changes) | ~90% off (cache read) |
| User / Assistant | Conversation history | **full price, every turn** |
| User | Latest message + Zep context data | full price |

**`system-message` (cache-preserving).** The system prompt is static. Each turn's context block arrives as a trailing user-data message after the latest user message. It is after the moving cache breakpoint, so it is not cached. On the next turn, the prior context data is replaced with a fresh block:

| Role | Content | Billed at |
|---|---|---|
| System | Static prompt + tools (never changes) | ~90% off (cache read) |
| User / Assistant | Conversation history | ~90% off (cache read) |
| User | Latest message (+ prior turn's reply entering the cache) | cache write (1.25x, cached for next turn) |
| **User** | **Fresh Zep context data** (replaced each turn, never cached) | full price, once |

This is a trailing user-data message. The system prompt contains only application-authored instructions.

The agent (`agent.py`) is identical in both modes — same memory retrieval, same model, same parameters, caching enabled in both. The variable is placement, which determines how much of the prompt caching can actually protect.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in both keys
```

- **Zep**: sign up at [app.getzep.com](https://app.getzep.com) and create an API key
- **Anthropic**: get a key from [platform.claude.com](https://platform.claude.com)

> **Requirements**: Prompt caching is opt-in. This example uses [explicit cache breakpoints](https://platform.claude.com/docs/en/build-with-claude/prompt-caching): the baseline places one on its static system block, and system-message mode places two — on the static system prompt and the latest user turn. Retrieved context is not cached.

## 1. Seed the demo user

```bash
python ingest.py
```

This creates a fixed demo user (`claude-caching-demo-dana`), ingests **two prior conversations (10 messages each)** into their Zep graph, and polls until entity/fact extraction finishes (a couple of minutes). The CLI and benchmark both use this same user, so the agent starts every session with genuine cross-session memory. The context block Zep returns is its default [Context Block](https://help.getzep.com/retrieving-context#zeps-context-block) — no custom templates.

Re-seed from scratch with `python ingest.py --recreate`.

## 2. Chat with the agent

```bash
# Cache-preserving mode (default)
python chat.py

# Baseline: context in the current user message (only the static prefix caches)
python chat.py --context-mode system-prompt
```

Each session opens a fresh thread for the demo user — ask *"where should I take the team for dinner?"* and the agent already knows about the dietary restrictions from the seeded prior conversations.

After every reply a stats line shows the turn's cache reads, cache writes, uncached input, cost, and latency. In `system-message` mode, cache reads keep pace with the whole conversation; in `system-prompt` mode the static prefix is still read from cache, but the growing history is re-billed at full price every turn.

Useful commands inside the REPL: `/context` (show the last retrieved memory block), `/stats` (session totals), `/quit`.

## 3. Run the benchmark

```bash
python benchmark.py --conversation short   # 6 turns
python benchmark.py --conversation long    # 18 turns
python benchmark.py --conversation xlong   # 36 turns
python benchmark.py --conversation xxlong  # 54 turns
```

The agent runs with a production-scale static prefix (~12k tokens): the Mira persona, a realistic operations manual (playbooks, message templates, worked examples, FAQs), and 12 tool definitions. That's the scale real agents run at — Claude Code carries ~16-19k tokens of tool definitions alone. The tools are passed with `tool_choice: "none"` so the model never invokes them: they occupy the prompt exactly as in production while keeping every benchmark turn a plain text response, so runs stay comparable.

The benchmark (requires the seeded demo user from step 1):

1. **Captures** the default Zep context block returned on each turn of a scripted conversation played into a fresh thread, polling between turns so extraction keeps pace — as it would in a real-paced conversation.
2. **Replays** the identical conversation through Claude Opus 4.8 twice — once per mode. Both replays use the same captured context blocks, the same scripted assistant replies (the model is called for real every turn and measured, but the scripted reply is what enters history — holding responses fixed isolates the variable being tested), and the same replace-each-turn context handling. The prompts are token-for-token equivalent across modes; the only difference is where the context block sits. Each replay gets a unique salt in its system prompt so runs can't hit each other's cache.
3. **Reports** per-turn and aggregate tokens, cost, and latency, and saves everything (including the captured context blocks) to `results/`.

Re-run the model comparison without touching Zep:

```bash
python benchmark.py --reuse results/<file>.json
```

### Reading the results

- **Cache hit rate** — the % of prompt tokens served from cache. Note that *both* modes show substantial hit rates (the static prefix dominates early turns in either placement) — the hit rate isn't the goal in itself; what matters is *which* tokens can be cached, and only `system-message` mode can cache the conversation history that grows every turn.
- **Input cost** — where the savings live: cache reads bill at $0.50/MTok vs $5.00/MTok base input (and $6.25/MTok cache writes) on Opus 4.8. This is the fully controlled comparison; total cost also includes output tokens, which are model-generated and vary slightly between runs.
- **Equivalent cost w/o caching** — what the same token stream would cost with caching disabled entirely, derived from the measured token counts. Both modes cache, so both come in under this number; it's the yardstick for how much caching saves in absolute terms.
- **TTFT** — time to first token, measured client-side. Single turns are noisy and at these prompt sizes the two modes land close; the cost columns, not latency, are where the difference shows.

Savings scale with the size of the stable prefix and the length of the conversation: the bigger the static prefix (persona, playbooks, tool schemas), the more there is for caching to protect. Pricing constants live in `pricing.py`.

## How the pieces fit

```
chat.py / benchmark.py
        │
        ▼
agent.py ── ZepMemoryAgent._build_request()   ← the only place the modes differ
        │
        ├── ZepMemory ──► zep.thread.add_messages(..., return_context=True)
        │                 (persists the message + retrieves context in one call)
        │
        └── anthropic.messages.stream(model, system, messages)
            (system-message mode marks explicit cache breakpoints in the system field and user messages)
```

- `scenario.py` — the demo user ID, the static prefix (persona + operations manual + tool definitions), the prior conversations that seed the graph, and the scripted benchmark conversations
- `ingest.py` — one-time seeding: creates the demo user, ingests the prior conversations, polls until processed
- `agent.py` — the agent, the two placement modes, per-turn metrics, Zep polling helpers
- `chat.py` — interactive CLI
- `benchmark.py` — capture → replay × 2 → report
- `pricing.py` — Opus 4.8 pricing constants and cost math
- `test_structure.py` — offline checks (no API keys needed) that both modes build valid, placement-rule-compliant requests

## Notes on the pattern

- **Replacing the context message is cache-safe — because of where it sits.** The user-data context message sits **after the last cache breakpoint** — it never enters any cache entry, so replacing it each turn invalidates nothing. The general lesson: where a message sits relative to your breakpoints determines whether changing it costs anything.
- **Each context block is paid for exactly once.** Fresh block at full input price, dropped next turn — no write premium, no accumulating re-reads. The model always sees exactly one, current memory block per turn.
- **Security.** Retrieved context is data, not an instruction. Keep it in user messages. Keep application-authored instructions in the system prompt.
- **Keeping blocks compact.** The fresh context block is the one full-price part of every turn, so its size sets your per-turn floor. Use Zep [context templates](https://help.getzep.com/context-templates) to cap facts/entities per block if you want to lower it.
