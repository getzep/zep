"""
Zep Cloud + DaoXE (OpenAI-compatible Chat Completions)

Demonstrates AsyncZep memory with AsyncOpenAI pointed at DaoXE:

    AsyncOpenAI(
        api_key=os.environ["DAOXE_API_KEY"],
        base_url="https://daoxe.com/v1",  # or DAOXE_BASE_URL
    )

DaoXE is a multi-model, multi-protocol API gateway (OpenAI Chat Completions,
OpenAI Responses, Anthropic Messages / Claude protocol, and more). This sample
uses only the OpenAI Python SDK + custom base URL path so it stays close to
existing OpenAI-style Zep examples.

Model IDs are account-scoped: set DAOXE_MODEL to an exact ID from your DaoXE
catalog (dashboard or authenticated GET /v1/models). Do not hardcode a static
public model list.

Availability: DaoXE is not offered in mainland China. Use the regional guidance
on the DaoXE site if your network cannot reach daoxe.com.
"""

from __future__ import annotations

import asyncio
import os
import uuid

from dotenv import load_dotenv
from openai import AsyncOpenAI
from zep_cloud.client import AsyncZep
from zep_cloud.types import Message

load_dotenv()

ZEP_API_KEY = os.environ.get("ZEP_API_KEY")
DAOXE_API_KEY = os.environ.get("DAOXE_API_KEY")
DAOXE_MODEL = os.environ.get("DAOXE_MODEL")
DAOXE_BASE_URL = os.environ.get("DAOXE_BASE_URL", "https://daoxe.com/v1")

FIRST_NAME = "Alex"
LAST_NAME = "Rivera"
USER_FULL_NAME = f"{FIRST_NAME} {LAST_NAME}"


def require_env() -> None:
    missing = [
        name
        for name, value in (
            ("ZEP_API_KEY", ZEP_API_KEY),
            ("DAOXE_API_KEY", DAOXE_API_KEY),
            ("DAOXE_MODEL", DAOXE_MODEL),
        )
        if not value
    ]
    if missing:
        raise SystemExit(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and set them."
        )


async def seed_prior_thread(zep: AsyncZep, user_id: str) -> None:
    """Ingest a short prior conversation so memory context is non-empty."""
    thread_id = f"daoxe-seed-{uuid.uuid4().hex[:8]}"
    await zep.thread.create(thread_id=thread_id, user_id=user_id)

    prior = [
        Message(
            name=USER_FULL_NAME,
            role="user",
            content="I'm planning a weekend trip to Lisbon and I prefer walking tours over buses.",
        ),
        Message(
            name="AI Assistant",
            role="assistant",
            content="Great — I'll remember you prefer walking tours in Lisbon.",
        ),
        Message(
            name=USER_FULL_NAME,
            role="user",
            content="Also keep replies under three short sentences when possible.",
        ),
        Message(
            name="AI Assistant",
            role="assistant",
            content="Understood. I'll keep responses brief.",
        ),
    ]
    await zep.thread.add_messages(thread_id=thread_id, messages=prior)
    print(f"Seeded prior thread: {thread_id}")


async def chat_once(
    zep: AsyncZep,
    openai_client: AsyncOpenAI,
    *,
    user_id: str,
    thread_id: str,
    user_message: str,
) -> str:
    await zep.thread.add_messages(
        thread_id=thread_id,
        messages=[
            Message(name=USER_FULL_NAME, role="user", content=user_message),
        ],
    )

    memory = await zep.thread.get_user_context(thread_id=thread_id, mode="basic")
    context_block = memory.context or ""

    system_prompt = (
        "You are a helpful assistant with long-term memory from Zep. "
        "Use the memory context when it is relevant. Keep replies concise.\n\n"
        f"{context_block}"
    )

    stream = await openai_client.chat.completions.create(
        model=DAOXE_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        stream=True,
        max_tokens=256,
    )

    parts: list[str] = []
    async for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            parts.append(delta)
            print(delta, end="", flush=True)
    print()

    full_response = "".join(parts)
    if full_response:
        await zep.thread.add_messages(
            thread_id=thread_id,
            messages=[
                Message(
                    name="AI Assistant",
                    role="assistant",
                    content=full_response,
                ),
            ],
        )
    return full_response


async def main() -> None:
    require_env()

    zep = AsyncZep(api_key=ZEP_API_KEY)
    openai_client = AsyncOpenAI(
        api_key=DAOXE_API_KEY,
        base_url=DAOXE_BASE_URL,
    )

    suffix = uuid.uuid4().hex[:8]
    user_id = f"daoxe-demo-{suffix}"
    thread_id = f"daoxe-live-{suffix}"

    await zep.user.add(
        user_id=user_id,
        first_name=FIRST_NAME,
        last_name=LAST_NAME,
        email=f"{user_id}@example.com",
    )
    print(f"Created Zep user: {user_id}")

    await seed_prior_thread(zep, user_id)

    await zep.thread.create(thread_id=thread_id, user_id=user_id)
    print(f"Live thread: {thread_id}")
    print(f"DaoXE base_url: {DAOXE_BASE_URL}")
    print(f"DaoXE model: {DAOXE_MODEL}")
    print("---")

    prompt = (
        "Based on what you know about me, suggest one walking-friendly activity "
        "for my Lisbon weekend."
    )
    print(f"User: {prompt}\nAssistant: ", end="", flush=True)
    await chat_once(
        zep,
        openai_client,
        user_id=user_id,
        thread_id=thread_id,
        user_message=prompt,
    )


if __name__ == "__main__":
    asyncio.run(main())
