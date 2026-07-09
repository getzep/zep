"""
Basic AG2 + Zep Memory Example.

Demonstrates ZepMemoryManager's automatic memory loop (attach_to_agent):
every message the agent receives is persisted and used to refresh its
system message, and every reply the agent sends is persisted automatically
too -- no manual enrich_system_message()/add_messages() calls needed.

Also registers the search/add memory tools so the agent can look up and
store memories on its own.

Prerequisites:
    export ZEP_API_KEY="your-zep-cloud-api-key"
    export OPENAI_API_KEY="your-openai-api-key"
"""

import asyncio
import os
import uuid

from autogen import AssistantAgent, LLMConfig, UserProxyAgent
from zep_cloud.client import AsyncZep

from zep_ag2 import ZepMemoryManager, register_all_tools


async def main() -> None:
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])

    user_id = f"user_{uuid.uuid4().hex[:16]}"
    session_id = f"thread_{uuid.uuid4().hex[:16]}"

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

    # ZepMemoryManager lazily provisions the Zep user/thread on first use
    # (pass first_name/last_name/email so Zep can anchor the user's identity
    # node in the graph). Call ensure_user/ensure_thread out-of-band instead
    # if you want provisioning failures to surface loudly before the first turn.
    memory_mgr = ZepMemoryManager(
        zep,
        user_id=user_id,
        session_id=session_id,
        first_name="Alice",
        email="alice@example.com",
    )

    # Wire the automatic inject+persist loop onto the assistant. Every
    # incoming message is persisted and used to refresh the system message;
    # every outgoing reply is persisted as an assistant turn.
    memory_mgr.attach_to_agent(assistant)

    # Register Zep tools so the agent can also search and store memories
    # explicitly (pin-or-expose: scope/reranker/limit/mmr_lambda/center_node_uuid
    # are all model-visible by default -- see README for pinning options).
    register_all_tools(assistant, user_proxy, zep, user_id=user_id, session_id=session_id)

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
