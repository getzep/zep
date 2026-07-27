# Zep + CKG: Two-Layer Context Agent

An agent that combines Zep's episodic memory with [Compact Knowledge Graphs (CKG)](https://graphifymd.com) — giving it both user-specific history and authoritative domain knowledge.

## Why two layers?

| Layer | Source | Answers |
|-------|--------|---------|
| **Episodic** (Zep) | User's conversation history | Who is this user? What happened before? |
| **Semantic** (CKG) | Pre-built domain graph, SHA-256 anchored | What is true about this domain? |

Neither alone is complete. Zep knows the user; CKG knows the domain. Together, the agent personalizes accurate answers without hallucinating facts outside its knowledge.

## Architecture

```
User message
    ↓
[Zep]  thread.get_user_context()  →  user summary + facts from past sessions
    ↓
[CKG]  CKGRetriever               →  concept subgraph (prerequisites + dependents)
    ↓
[LLM]  synthesize from both layers
    ↓
[Zep]  thread.add_messages()      →  store exchange for future sessions
```

## Setup

```bash
pip install zep-cloud langchain-ckg langchain-openai langgraph

export ZEP_API_KEY=your-zep-api-key       # app.getzep.com
export OPENAI_API_KEY=your-openai-key
```

## Run

```bash
python agent.py
```

## Available CKG domains

97 domains available — NVIDIA AI, NemoClaw, Salesforce AgentForce, Nemotron, Perplexity, and more.

```python
from ckg_mcp.graph import list_domains
print(list_domains())
```

Change `ckg_domain` in the example to query any domain.

## What gets stored in Zep

Every exchange is written back to the Zep thread via `thread.add_messages()`. On the next invocation, `thread.get_user_context()` returns a context block that includes facts extracted from prior exchanges — so the agent remembers what the user asked before and what it answered.

## Links

- [Zep documentation](https://help.getzep.com)
- [CKG on PyPI](https://pypi.org/project/langchain-ckg/)
- [CKG benchmark](https://huggingface.co/datasets/danyarm/ckg-benchmark)
- [graphifymd.com](https://graphifymd.com)
