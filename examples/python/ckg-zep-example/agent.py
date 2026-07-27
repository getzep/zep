"""
Zep + CKG: Two-layer context agent.

Zep supplies the episodic layer — facts about this specific user
across past sessions.

CKG (Compact Knowledge Graph) supplies the semantic layer —
authoritative domain knowledge structured as a typed dependency graph,
SHA-256 anchored to source documents.

Together they give an agent both personal memory and domain expertise
without hallucination on either layer.

Architecture:
    User message
        ↓
    [Zep]   thread.get_user_context() → who this user is, what happened before
        ↓
    [CKG]   CKGRetriever → what is true about the domain they're asking about
        ↓
    [LLM]   synthesize from both context layers
        ↓
    [Zep]   thread.add_messages() → store exchange for future sessions

Prerequisites:
    pip install zep-cloud langchain-ckg langchain-openai langgraph

    Set environment variables:
        ZEP_API_KEY   — from app.getzep.com
        OPENAI_API_KEY
"""

from __future__ import annotations

import os
from typing import Annotated, TypedDict
import operator

from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from zep_cloud.client import Zep

from langchain_ckg import CKGRetriever

ZEP_API_KEY = os.environ["ZEP_API_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

zep = Zep(api_key=ZEP_API_KEY)
llm = ChatOpenAI(model="gpt-4o-mini", api_key=OPENAI_API_KEY)


# ── State ─────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    query: str
    user_id: str
    thread_id: str
    ckg_domain: str
    episodic_context: str       # from Zep — who/history
    semantic_context: str       # from CKG — domain knowledge
    messages: Annotated[list, operator.add]
    response: str


# ── Node: Zep episodic retrieval ───────────────────────────────────────────────

def retrieve_episodic(state: AgentState) -> dict:
    """Pull user memory from Zep — facts, history, and user summary."""
    try:
        user_context = zep.thread.get_user_context(thread_id=state["thread_id"])
        episodic = user_context.context or ""
    except Exception as e:
        # New thread/user has no prior context — treat as empty rather than failing.
        # Re-raise auth errors so misconfigurations surface immediately.
        if "auth" in str(e).lower() or "401" in str(e) or "403" in str(e):
            raise
        episodic = ""
    return {"episodic_context": episodic}


# ── Node: CKG semantic retrieval ───────────────────────────────────────────────

def retrieve_semantic(state: AgentState) -> dict:
    """Pull domain knowledge from a CKG — prerequisite + dependent concept graph."""
    retriever = CKGRetriever(domain=state["ckg_domain"], depth=3)
    docs = retriever.invoke(state["query"])
    semantic = "\n\n".join(d.page_content for d in docs)
    return {"semantic_context": semantic or "No matching domain concepts found."}


# ── Node: synthesize ───────────────────────────────────────────────────────────

_SYSTEM = """\
You are a precise assistant with access to two context layers:

EPISODIC MEMORY (Zep) — facts specific to this user from past sessions.
Use this to personalize your answer and avoid repeating what the user already knows.

SEMANTIC KNOWLEDGE (CKG) — authoritative domain knowledge, SHA-256 anchored.
Use this as your ground truth for factual claims. Do not add facts not present here.

Synthesize both layers into a single, accurate response.
"""

def synthesize(state: AgentState) -> dict:
    prompt = f"""EPISODIC MEMORY (this user's history):
{state["episodic_context"] or "No prior context available."}

SEMANTIC KNOWLEDGE (domain graph):
{state["semantic_context"]}

Question: {state["query"]}"""

    response = llm.invoke([
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": prompt},
    ])
    return {
        "response": response.content,
        "messages": [{"role": "assistant", "content": response.content}],
    }


# ── Node: store in Zep ─────────────────────────────────────────────────────────

def store_exchange(state: AgentState) -> dict:
    """Persist the exchange in Zep so it's available in future sessions."""
    from zep_cloud.types import Message
    zep.thread.add_messages(
        thread_id=state["thread_id"],
        messages=[
            Message(role="user", role_type="user", content=state["query"]),
            Message(role="assistant", role_type="assistant", content=state["response"]),
        ],
    )
    return {}


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(AgentState)

    g.add_node("retrieve_episodic", retrieve_episodic)
    g.add_node("retrieve_semantic", retrieve_semantic)
    g.add_node("synthesize", synthesize)
    g.add_node("store_exchange", store_exchange)

    g.set_entry_point("retrieve_episodic")
    g.add_edge("retrieve_episodic", "retrieve_semantic")
    g.add_edge("retrieve_semantic", "synthesize")
    g.add_edge("synthesize", "store_exchange")
    g.add_edge("store_exchange", END)

    return g.compile()


# ── Example run ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    agent = build_graph()

    # Ensure the user and thread exist in Zep before running.
    USER_ID = "user-123"
    THREAD_ID = "thread-456"

    try:
        zep.user.get(USER_ID)
    except Exception:
        zep.user.add(user_id=USER_ID)

    try:
        zep.thread.get(THREAD_ID)
    except Exception:
        zep.thread.add(thread_id=THREAD_ID, user_id=USER_ID)

    result = agent.invoke({
        "query": "What are the prerequisites for using NemoClaw kernel fusion?",
        "user_id": USER_ID,
        "thread_id": THREAD_ID,
        "ckg_domain": "nvidia-nemoclaw",   # any of 97 available CKG domains
        "episodic_context": "",
        "semantic_context": "",
        "messages": [],
        "response": "",
    })

    print(result["response"])
