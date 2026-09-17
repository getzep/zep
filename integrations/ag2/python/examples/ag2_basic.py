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
import uuid

from autogen import AssistantAgent, LLMConfig, UserProxyAgent
from zep_cloud.client import AsyncZep

from zep_ag2 import ZepMemoryManager, create_thread, create_user, register_all_tools


async def provision() -> tuple[str, str, str]:
    """Create the user and the thread, and return their UUIDs.

    This function uses its own client, because an AsyncZep client binds to
    the event loop that first drives a request. The synchronous AG2 hooks
    use a background loop, so the chat phase makes a second client.
    """
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])

    # One-time provisioning. Pass first_name/last_name/email so Zep can
    # anchor the identity node of the user in the graph.
    #
    # The user_id label is a temporary workaround for a defect in the
    # production v4 API, which rejects thread.add_messages and
    # thread.get_context for a user that has no label. The label is not an
    # address: the example uses user.uuid_ for every later call.
    user = await create_user(
        zep,
        user_id=f"ag2-basic-{uuid.uuid4().hex[:8]}",
        first_name="Alice",
        email="alice@example.com",
    )
    thread = await create_thread(zep, user_uuid=user.uuid_ or "")
    return user.uuid_ or "", user.graph_uuid or "", thread.uuid_ or ""


async def show_context(thread_uuid: str) -> None:
    """Poll the thread context, because Zep ingestion is asynchronous."""
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    for _ in range(12):
        response = await zep.thread.get_context(thread_uuid)
        context = (response.context or "").strip()
        if context:
            print("\n=== Zep Context Block ===")
            print(context)
            return
        await asyncio.sleep(10)
    print("\nNo context is available yet. Zep ingestion is asynchronous.")


def main() -> None:
    user_uuid, graph_uuid, thread_uuid = asyncio.run(provision())

    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])

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

    asyncio.run(show_context(thread_uuid))


if __name__ == "__main__":
    main()
