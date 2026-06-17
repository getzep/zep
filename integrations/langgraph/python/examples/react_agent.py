"""
LangGraph ``create_react_agent`` with Zep long-term memory (primary path).

This example wires Zep into a prebuilt ReAct agent using the node/tool helpers:

  * a ``prompt`` callable injects the user's Zep Context Block into the system
    prompt on every turn (via :func:`build_system_message`),
  * a graph-search tool (:func:`create_graph_search_tool`) lets the model search
    the knowledge graph on demand,
  * :func:`persist_messages` writes each turn back to Zep.

It seeds a couple of facts, waits for Zep to build the graph, then asks a recall
question to show memory working across turns.

Prerequisites::

    pip install zep-langgraph langchain-openai
    export ZEP_API_KEY="your-zep-api-key"
    export OPENAI_API_KEY="your-openai-api-key"

Run::

    python examples/react_agent.py
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from zep_cloud import Message
from zep_cloud.client import AsyncZep

from zep_langgraph import (
    build_system_message,
    create_graph_search_tool,
    persist_messages,
)

ZEP_API_KEY = os.environ.get("ZEP_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

if not ZEP_API_KEY:
    raise OSError("ZEP_API_KEY is not set.")
if not OPENAI_API_KEY:
    raise OSError("OPENAI_API_KEY is not set.")

_suffix = uuid4().hex[:8]
USER_ID = f"langgraph-example-{_suffix}"
THREAD_ID = f"langgraph-example-thread-{_suffix}"
FIRST_NAME = "Alice"
LAST_NAME = "Smith"

BASE_INSTRUCTIONS = (
    "You are a helpful assistant with long-term memory. When memory context is "
    "provided, use it to give personalised, memory-aware answers. You may also "
    "call the search_memory tool to look up specific details on demand."
)


async def main() -> None:
    zep = AsyncZep(api_key=ZEP_API_KEY)

    print("=" * 60)
    print("LangGraph + Zep (create_react_agent, primary path)")
    print(f"  user_id={USER_ID}  thread_id={THREAD_ID}")
    print("=" * 60)

    # --- One-time Zep setup: create the user and thread out-of-band. ---
    await zep.user.add(user_id=USER_ID, first_name=FIRST_NAME, last_name=LAST_NAME)
    await zep.thread.create(thread_id=THREAD_ID, user_id=USER_ID)

    # --- Prompt callable: inject the Zep Context Block on every turn. ---
    async def prompt(state: dict) -> list:
        system = await build_system_message(
            zep,
            thread_id=THREAD_ID,
            base_instructions=BASE_INSTRUCTIONS,
        )
        return [system, *state["messages"]]

    # --- On-demand graph search over the user's personal graph. ---
    search_tool = create_graph_search_tool(zep, user_id=USER_ID, scope="edges")

    # gpt-5 is a reasoning model and rejects an explicit ``temperature``; omit it.
    model = ChatOpenAI(model="gpt-5")
    agent = create_react_agent(model=model, tools=[search_tool], prompt=prompt)

    async def chat(user_text: str) -> str:
        """Run one turn through the agent and persist it to Zep."""
        result = await agent.ainvoke({"messages": [HumanMessage(content=user_text)]})
        reply = result["messages"][-1]
        reply_text = reply.content if isinstance(reply.content, str) else str(reply.content)

        # Persist the user turn and the assistant reply to Zep.
        await persist_messages(
            zep,
            thread_id=THREAD_ID,
            messages=[
                Message(role="user", content=user_text, name=f"{FIRST_NAME} {LAST_NAME}"),
                AIMessage(content=reply_text),
            ],
        )
        return reply_text

    # --- Phase 1: seed facts ---
    print("\n--- Seeding facts ---")
    for text in [
        "My name is Alice and I'm a software engineer at Acme Corp.",
        "I live in Portland and love hiking on weekends.",
    ]:
        print(f"User:  {text}")
        print(f"Agent: {await chat(text)}\n")

    # --- Phase 2: wait for asynchronous graph ingestion ---
    print("--- Waiting 15s for Zep to build the graph ---\n")
    await asyncio.sleep(15)

    # --- Phase 3: recall ---
    print("--- Testing recall ---")
    for text in ["Where do I work?", "What do I like to do on weekends?"]:
        print(f"User:  {text}")
        print(f"Agent: {await chat(text)}\n")

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
