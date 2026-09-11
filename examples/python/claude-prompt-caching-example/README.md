# Zep + Claude Opus 4.8: Cache-Preserving Memory Injection

Memory-powered agents have always faced an awkward trade-off: the freshest place to put retrieved memory is the system prompt, but changing the system prompt every turn means **none of the conversation that follows it can be cached** — so you re-pay full input price for the entire history on every turn.

Claude Opus 4.8 removes the trade-off with [mid-conversation system messages](https://platform.claude.com/docs/en/build-with-claude/mid-conversation-system-messages): `{"role": "system"}` entries appended *inside* the messages array, after the user turn. The cached prefix stays byte-identical, the context still carries operator-level (system) authority, and every turn reads the whole conversation from cache.

This example wires [Zep](https://www.getzep.com/) into that pattern and **measures the difference**.

## The two setups

**`system-prompt` (the baseline — prefix-only caching).** The Zep context block rides in the system prompt as a second system block that changes every turn. Caching is enabled and used as well as this placement allows: a breakpoint on the static first block caches the persona and tools. But caching hashes the request prefix in order (tools → system → messages), so everything after the changing context block — the entire conversation history — can never be cached, and history is what grows with every turn:

| Role | Content | Billed at |
|---|---|---|
| System (block 1) | Static prompt + tools (never changes) | ~90% off (cache read) |
| System (block 2) | **Zep context block (changes every turn)** | full price, every turn |
| User / Assistant | Conversation history | **full price, every turn** |
| User | Latest message | full price |

**`system-message` (Claude Opus 4.8, cache-preserving).** The system prompt is static; each turn's context block arrives as a mid-conversation system message at the *very end* of the messages array, right after the latest user message — and **after the moving cache breakpoint, so it is never cached**. On the next turn the prior context message is simply replaced with a fresh one, which invalidates nothing (it was never part of any cache entry). Everything before it — the static prefix and the entire conversation history — stays byte-identical and reads from cache:

| Role | Content | Billed at |
|---|---|---|
| System | Static prompt + tools (never changes) | ~90% off (cache read) |
| User / Assistant | Conversation history | ~90% off (cache read) |
| User | Latest message (+ prior turn's reply entering the cache) | cache write (1.25x, cached for next turn) |
| **System** | **Fresh Zep context block** (replaced each turn, never cached) | full price, once |

This is the trailing ["context message" pattern Zep has long recommended](https://help.getzep.com/retrieving-context) for cache efficiency — with one upgrade: Opus 4.8 lets that message be a true `system` message, giving retrieved memory operator-level authority instead of riding in a faked user or tool turn.

The agent (`agent.py`) is identical in both modes — same memory retrieval, same model, same parameters, caching enabled in both. The variable is placement, which determines how much of the prompt caching can actually protect.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in both keys
```

- **Zep**: sign up at [app.getzep.com](https://app.getzep.com) and create an API key
- **Anthropic**: get a key from [platform.claude.com](https://platform.claude.com)

> **Requirements**: mid-conversation system messages are available on **Claude Opus 4.8 only**, on the Claude API (not Bedrock/Vertex/Foundry). Prompt caching is opt-in — this example uses [explicit cache breakpoints](https://platform.claude.com/docs/en/build-with-claude/prompt-caching): the baseline places one on its static system block (the most that placement can cache), and the system-message mode places two — end of the static system prompt and the latest **user** message (moved forward each turn), never on the trailing context message. (Note: the SDK's *automatic* top-level `cache_control` field silently fails to engage when the messages array ends with a `role: "system"` entry — explicit breakpoints sidestep that.)

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

# Baseline: context in the system prompt (only the static prefix caches)
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
            (system-message mode marks explicit cache breakpoints inside system/messages)
```

- `scenario.py` — the demo user ID, the static prefix (persona + operations manual + tool definitions), the prior conversations that seed the graph, and the scripted benchmark conversations
- `ingest.py` — one-time seeding: creates the demo user, ingests the prior conversations, polls until processed
- `agent.py` — the agent, the two placement modes, per-turn metrics, Zep polling helpers
- `chat.py` — interactive CLI
- `benchmark.py` — capture → replay × 2 → report
- `pricing.py` — Opus 4.8 pricing constants and cost math
- `test_structure.py` — offline checks (no API keys needed) that both modes build valid, placement-rule-compliant requests

## Notes on the pattern

- **Replacing the context message is cache-safe — because of where it sits.** Anthropic's docs warn against editing or removing an already-sent mid-conversation system message, and the stated reason is that doing so *"invalidates the cache from that point forward."* That applies to messages inside the cached prefix. The context message here sits **after the last cache breakpoint** — it never enters any cache entry, so replacing it each turn invalidates nothing. The general lesson: where a message sits relative to your breakpoints determines whether changing it costs anything.
- **Each context block is paid for exactly once.** Fresh block at full input price, dropped next turn — no write premium, no accumulating re-reads. The model always sees exactly one, current memory block per turn.
- **Authority.** System-role placement gives memory operator-level priority. Zep context blocks are structured summaries generated by your own memory infrastructure — not raw third-party text, which Anthropic [recommends keeping out of system messages](https://platform.claude.com/docs/en/build-with-claude/mid-conversation-system-messages#limitations). Treat your memory-write path accordingly.
- **Keeping blocks compact.** The fresh context block is the one full-price part of every turn, so its size sets your per-turn floor. Use Zep [context templates](https://help.getzep.com/context-templates) to cap facts/entities per block if you want to lower it.
