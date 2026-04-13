"""
Manual integration test for zep-ag2.

Run this to verify the integration works end-to-end with real Zep and OpenAI APIs.

Prerequisites:
    export ZEP_API_KEY="your-zep-cloud-api-key"
    export OPENAI_API_KEY="your-openai-api-key"

Usage:
    cd integrations/python/zep_ag2
    source .venv/bin/activate
    python examples/manual_test.py
"""

import asyncio
import os
import sys
import uuid


def check_env() -> bool:
    """Check required environment variables."""
    ok = True
    for var in ("ZEP_API_KEY", "OPENAI_API_KEY"):
        if not os.environ.get(var):
            print(f"  MISSING: {var}")
            ok = False
        else:
            print(f"  OK: {var} is set")
    return ok


async def test_1_imports() -> bool:
    """Test 1: Verify all imports work."""
    print("\n=== Test 1: Imports ===")
    try:
        from zep_ag2 import (  # noqa: F401
            ZepGraphMemoryManager,
            ZepMemoryManager,
            create_add_graph_data_tool,
            create_add_memory_tool,
            create_search_graph_tool,
            create_search_memory_tool,
            register_all_tools,
        )

        print("  OK: All zep_ag2 imports successful")

        from autogen import (  # noqa: F401
            AssistantAgent,
            ConversableAgent,
            LLMConfig,
            UserProxyAgent,
        )

        print("  OK: All AG2 imports successful")

        from zep_cloud.client import AsyncZep  # noqa: F401

        print("  OK: Zep Cloud SDK import successful")
        return True
    except ImportError as e:
        print(f"  FAIL: {e}")
        return False


async def test_2_zep_connection() -> bool:
    """Test 2: Verify Zep API connection."""
    print("\n=== Test 2: Zep API Connection ===")
    from zep_cloud.client import AsyncZep

    try:
        zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
        # Try listing users as a connectivity check
        user_id = f"test_ag2_{uuid.uuid4().hex[:8]}"
        await zep.user.add(user_id=user_id, first_name="TestUser")
        print(f"  OK: Created test user '{user_id}'")

        # Clean up
        await zep.user.delete(user_id)
        print(f"  OK: Deleted test user '{user_id}'")
        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


async def test_3_memory_manager() -> bool:
    """Test 3: ZepMemoryManager — create, enrich, add messages."""
    print("\n=== Test 3: ZepMemoryManager ===")
    from zep_cloud.client import AsyncZep

    from zep_ag2 import ZepMemoryManager

    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    user_id = f"test_ag2_{uuid.uuid4().hex[:8]}"
    thread_id = f"thread_{uuid.uuid4().hex[:8]}"

    try:
        # Setup
        await zep.user.add(user_id=user_id, first_name="Alice")
        await zep.thread.create(thread_id=thread_id, user_id=user_id)
        print(f"  OK: Created user '{user_id}' and thread '{thread_id}'")

        # Create manager
        mgr = ZepMemoryManager(zep, user_id=user_id, session_id=thread_id)
        print(f"  OK: ZepMemoryManager created (user={mgr.user_id}, session={mgr.session_id})")

        # Add messages
        await mgr.add_messages(
            [
                {"content": "Hi, I'm Alice and I love hiking.", "role": "user", "name": "Alice"},
                {"content": "Nice to meet you, Alice! Hiking is great.", "role": "assistant"},
            ]
        )
        print("  OK: Added 2 messages to thread")

        # Wait for Zep to process
        print("  ... waiting 3s for Zep indexing")
        await asyncio.sleep(3)

        # Get context
        context = await mgr.get_memory_context()
        print(f"  OK: Got memory context ({len(context)} chars)")
        if context:
            print(f"  Preview: {context[:200]}...")

        # Get facts
        facts = await mgr.get_session_facts()
        print(f"  OK: Got {len(facts)} session facts")

        # Test enrich_system_message with a mock agent
        from unittest.mock import MagicMock

        mock_agent = MagicMock()
        mock_agent.system_message = "You are helpful."
        mock_agent.update_system_message = MagicMock()
        await mgr.enrich_system_message(mock_agent)
        if mock_agent.update_system_message.called:
            enriched = mock_agent.update_system_message.call_args[0][0]
            print(f"  OK: System message enriched ({len(enriched)} chars)")
        else:
            print("  WARN: No memory context to inject (may need more indexing time)")

        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        try:
            await zep.thread.delete(thread_id)
            await zep.user.delete(user_id)
            print("  OK: Cleaned up user and thread")
        except Exception:
            pass


async def test_4_tool_factories() -> bool:
    """Test 4: Tool factories — create tools and call them against real Zep."""
    print("\n=== Test 4: Tool Factories ===")
    from zep_cloud.client import AsyncZep

    from zep_ag2 import (
        create_add_graph_data_tool,
        create_add_memory_tool,
        create_search_graph_tool,
        create_search_memory_tool,
    )

    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    user_id = f"test_ag2_{uuid.uuid4().hex[:8]}"
    thread_id = f"thread_{uuid.uuid4().hex[:8]}"

    try:
        await zep.user.add(user_id=user_id, first_name="Bob")
        await zep.thread.create(thread_id=thread_id, user_id=user_id)

        # Create tools
        search_mem = create_search_memory_tool(zep, user_id=user_id)
        add_mem = create_add_memory_tool(zep, user_id=user_id, session_id=thread_id)
        search_graph = create_search_graph_tool(zep, user_id=user_id)
        add_graph = create_add_graph_data_tool(zep, user_id=user_id)
        print("  OK: All 4 tool factories created tools")

        # Test add_memory tool
        result = add_mem(content="Bob is a data scientist who loves Python.", role="user")
        print(f"  OK: add_memory -> {result}")

        # Test add_graph_data tool
        result = add_graph(data="Bob specializes in NLP and machine learning.")
        print(f"  OK: add_graph_data -> {result}")

        # Wait for indexing
        print("  ... waiting 5s for Zep indexing")
        await asyncio.sleep(5)

        # Test search_memory tool
        result = search_mem(query="data science", limit=3)
        print(f"  OK: search_memory -> {result[:200]}...")

        # Test search_graph tool
        result = search_graph(query="NLP", limit=3)
        print(f"  OK: search_graph -> {result[:200]}...")

        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        try:
            await zep.thread.delete(thread_id)
            await zep.user.delete(user_id)
            print("  OK: Cleaned up")
        except Exception:
            pass


async def test_5_ag2_agent_with_tools() -> bool:
    """Test 5: Full AG2 agent with Zep tools — the real integration test."""
    print("\n=== Test 5: AG2 Agent with Zep Tools ===")
    from zep_cloud.client import AsyncZep

    from zep_ag2 import ZepMemoryManager, register_all_tools

    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    user_id = f"test_ag2_{uuid.uuid4().hex[:8]}"
    thread_id = f"thread_{uuid.uuid4().hex[:8]}"

    try:
        await zep.user.add(user_id=user_id, first_name="Charlie")
        await zep.thread.create(thread_id=thread_id, user_id=user_id)
        print(f"  OK: Created user '{user_id}' and thread '{thread_id}'")

        from autogen import AssistantAgent, LLMConfig, UserProxyAgent

        llm_config = LLMConfig(
            {
                "model": "gpt-4o-mini",
                "api_key": os.environ["OPENAI_API_KEY"],
                "temperature": 0,
            }
        )

        assistant = AssistantAgent(
            name="assistant",
            llm_config=llm_config,
            system_message=(
                "You are a helpful assistant with long-term memory. "
                "Use the search_memory and add_memory tools to remember things. "
                "When the user tells you something, store it using add_memory. "
                "When asked a question, search your memory first."
            ),
        )
        user_proxy = UserProxyAgent(
            name="user",
            human_input_mode="NEVER",
            code_execution_config=False,
            is_termination_msg=lambda msg: "TERMINATE" in (msg.get("content") or ""),
        )
        print("  OK: AG2 agents created")

        # Register all Zep tools
        tools = register_all_tools(
            assistant, user_proxy, zep, user_id=user_id, session_id=thread_id
        )
        print(f"  OK: Registered {len(tools)} tools: {list(tools.keys())}")

        # Enrich with any existing memory
        mgr = ZepMemoryManager(zep, user_id=user_id, session_id=thread_id)
        await mgr.enrich_system_message(assistant)
        print("  OK: System message enriched")

        # Run a short conversation
        print("  Running conversation (max 4 turns)...")
        result = user_proxy.initiate_chat(
            assistant,
            message="Please remember that my name is Charlie and I'm a software engineer who loves Rust. Say TERMINATE when done.",
            max_turns=4,
        )
        print(f"  OK: Conversation completed ({len(result.chat_history)} messages)")
        for i, msg in enumerate(result.chat_history):
            role = msg.get("role", msg.get("name", "?"))
            content = str(msg.get("content", ""))[:100]
            print(f"    [{i}] {role}: {content}")

        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        try:
            await zep.thread.delete(thread_id)
            await zep.user.delete(user_id)
        except Exception:
            pass


async def main() -> None:
    print("=" * 60)
    print("zep-ag2 Manual Integration Test")
    print("=" * 60)

    print("\nChecking environment variables:")
    if not check_env():
        print("\nSet the missing environment variables and try again.")
        sys.exit(1)

    results: dict[str, bool] = {}

    results["1_imports"] = await test_1_imports()
    results["2_zep_connection"] = await test_2_zep_connection()
    results["3_memory_manager"] = await test_3_memory_manager()
    results["4_tool_factories"] = await test_4_tool_factories()
    results["5_ag2_agent"] = await test_5_ag2_agent_with_tools()

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    all_pass = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {status}: {name}")
        if not passed:
            all_pass = False

    print()
    if all_pass:
        print("All tests passed!")
    else:
        print("Some tests failed — check output above.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
