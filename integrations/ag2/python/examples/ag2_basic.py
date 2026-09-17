"""
Basic AG2 + Zep Memory Example.

Demonstrates ZepMemoryManager's automatic memory loop (attach_to_agent):
every message the agent receives is persisted and used to refresh its
system message, and every reply the agent sends is persisted automatically
too -- no manual enrich_system_message()/add_messages() calls needed.

Also registers the search/add memory tools so the agent can look up and
store memories on its own.

Zep v4 addresses every user, thread, and graph by a server-generated UUID.
The example creates the user and the thread one time and keeps the UUIDs
from the responses. A production application stores those UUIDs in its own
database.

Prerequisites:
    export ZEP_API_KEY="your-zep-cloud-api-key"
    export OPENAI_API_KEY="your-openai-api-key"
"""

import asyncio
import os

from autogen import AssistantAgent, LLMConfig, UserProxyAgent
from zep_cloud.client import AsyncZep

from zep_ag2 import ZepMemoryManager, create_thread, create_user, register_all_tools


async def main() -> None:
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])

    # One-time provisioning. Pass first_name/last_name/email so Zep can
    # anchor the identity node of the user in the graph.
    user = await create_user(zep, first_name="Alice", email="alice@example.com")
    thread = await create_thread(zep, user_uuid=user.uuid_ or "")

    user_uuid = user.uuid_ or ""
    graph_uuid = user.graph_uuid or ""
    thread_uuid = thread.uuid_ or ""

    # Configure AG2 agents
    llm_config = LLMConfig({"model": "gpt-5-mini", "api_key": os.environ["OPENAI_API_KEY"]})

    assistant = AssistantAgent(
        name="assistant",
        llm_config=llm_config,
        system_message="You are a helpful assistant with long-term memory.",
    )
    user_proxy = UserProxyAgent(
        name="user",
        human_input_mode="NEVER",
        code_execution_config=False,
        is_termination_msg=lambda msg: "TERMINATE" in (msg.get("content") or ""),
    )

    memory_mgr = ZepMemoryManager(zep, user_uuid, thread_uuid, graph_uuid=graph_uuid)

    # Wire the automatic inject+persist loop onto the assistant. Every
    # incoming message is persisted and used to refresh the system message;
    # every outgoing reply is persisted as an assistant turn.
    memory_mgr.attach_to_agent(assistant)

    # Register Zep tools so the agent can also search and store memories
    # explicitly (pin-or-expose: scope/reranker/limit/mmr_lambda/center_node_uuid
    # are all model-visible by default -- see README for pinning options).
    register_all_tools(assistant, user_proxy, zep, graph_uuid, thread_uuid)

    # Run a conversation -- no manual memory calls needed per turn.
    try:
        result = user_proxy.initiate_chat(
            assistant,
            message="My name is Alice and I love hiking in the mountains. Remember that! Say TERMINATE when done.",
            max_turns=4,
        )
        print(result)
    except Exception as e:
        print(f"Conversation error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
