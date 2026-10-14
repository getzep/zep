"""Test the proxy locally without ElevenLabs."""

import asyncio
import json
import os
import time
import uuid

import httpx
from dotenv import load_dotenv
from zep_cloud.client import AsyncZep
from zep_cloud.core.api_error import ApiError

load_dotenv()

PROXY_API_KEY = os.getenv("PROXY_API_KEY")
PROXY_URL = "http://localhost:8080"


async def wait_for_task(zep: AsyncZep, task_uuid: str) -> None:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        task = await zep.task.get(task_uuid)
        if task.status == "succeeded":
            return
        if task.status in {"partial", "failed"}:
            raise RuntimeError(f"Zep task ended with {task.status}: {task.error}")
        await asyncio.sleep(2)
    raise TimeoutError(f"Timed out waiting for Zep task {task_uuid}")


async def wait_for_delete(zep: AsyncZep, result) -> None:
    task = getattr(result, "task", None)
    if task and task.uuid_:
        await wait_for_task(zep, task.uuid_)


async def send_chat_turn(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    user_uuid: str,
    thread_uuid: str,
    messages: list[dict[str, str]],
) -> str:
    request_body = {
        "model": "gpt-5-mini",
        "messages": messages,
        "stream": True,
        "elevenlabs_extra_body": {
            "user_id": user_uuid,
            "conversation_id": thread_uuid,
        },
    }
    full_response = ""
    async with client.stream(
        "POST",
        f"{PROXY_URL}/v1/chat/completions",
        json=request_body,
        headers=headers,
        timeout=120.0,
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if chunk.get("choices"):
                content = chunk["choices"][0].get("delta", {}).get("content")
                if content:
                    full_response += content
    return full_response


async def test_proxy() -> None:
    if not PROXY_API_KEY:
        raise RuntimeError("Set PROXY_API_KEY before running this test")
    if not os.getenv("ZEP_API_KEY"):
        raise RuntimeError("Set ZEP_API_KEY before running this test")

    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    user_uuid = None
    thread_uuid = None
    try:
        async with httpx.AsyncClient() as client:
            suffix = uuid.uuid4().hex[:8]
            user_response = await client.post(
                f"{PROXY_URL}/zep/users",
                json={
                    "first_name": f"examples-v4-e2e-{suffix}",
                    "last_name": "ElevenLabs",
                    "email": f"examples-v4-e2e-{suffix}@example.com",
                },
                timeout=30.0,
            )
            user_response.raise_for_status()
            user_uuid = user_response.json()["user_uuid"]

            thread_response = await client.post(
                f"{PROXY_URL}/zep/threads",
                json={"user_uuid": user_uuid},
                timeout=30.0,
            )
            thread_response.raise_for_status()
            thread_uuid = thread_response.json()["thread_uuid"]

            headers = {"Authorization": f"Bearer {PROXY_API_KEY}"}

            episode = await zep.graph.episode.add(
                user_uuid,
                type="text",
                data="The test user's favorite color is purple.",
            )
            if episode.task and episode.task.uuid_:
                await wait_for_task(zep, episode.task.uuid_)

            history = [
                {
                    "role": "system",
                    "content": "You are a helpful voice assistant. Use the user's memory when it is relevant.",
                },
                {"role": "user", "content": "Please remember that I like purple."},
            ]
            first_reply = await send_chat_turn(
                client, headers, user_uuid, thread_uuid, history
            )
            history.extend(
                [
                    {"role": "assistant", "content": first_reply},
                    {"role": "user", "content": "What is my favorite color?"},
                ]
            )
            second_reply = await send_chat_turn(
                client, headers, user_uuid, thread_uuid, history
            )
            if "purple" not in second_reply.lower():
                raise AssertionError(
                    f"Second reply did not use the seeded memory: {second_reply}"
                )

            context = await zep.thread.get_context(thread_uuid)
            if not context.context:
                raise AssertionError("Zep returned no context after the second turn")

            deadline = time.monotonic() + 30
            messages = None
            while time.monotonic() < deadline:
                messages = await zep.thread.list_messages(
                    thread_uuid=thread_uuid,
                    page_size=100,
                )
                if len(messages.items or []) >= 4:
                    break
                await asyncio.sleep(1)
            message_count = len(messages.items or []) if messages else 0
            if message_count < 4:
                raise AssertionError(
                    f"Expected four persisted messages after two turns, found {message_count}"
                )

            print(f"Created user UUID: {user_uuid}")
            print(f"Created thread UUID: {thread_uuid}")
            print(f"First-turn response length: {len(first_reply)} characters")
            print(f"Second-turn response: {second_reply}")
            print(f"Context after the second turn: {len(context.context)} characters")
            print(f"Persisted messages: {message_count}")
    finally:
        if thread_uuid:
            try:
                result = await zep.thread.delete(thread_uuid=thread_uuid)
                await wait_for_delete(zep, result)
            except (ApiError, RuntimeError, TimeoutError, httpx.HTTPError) as e:
                print(f"Could not delete test thread {thread_uuid}: {e}")
        if user_uuid:
            try:
                result = await zep.graph.delete(user_uuid)
                await wait_for_delete(zep, result)
            except (ApiError, RuntimeError, TimeoutError, httpx.HTTPError) as e:
                print(f"Could not delete test graph {user_uuid}: {e}")
            try:
                result = await zep.user.delete(user_uuid=user_uuid)
                await wait_for_delete(zep, result)
            except (ApiError, RuntimeError, TimeoutError, httpx.HTTPError) as e:
                print(f"Could not delete test user {user_uuid}: {e}")


async def main() -> None:
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{PROXY_URL}/health", timeout=5.0)
        response.raise_for_status()
        print(f"Proxy health: {response.json()}")
    await test_proxy()


if __name__ == "__main__":
    asyncio.run(main())
