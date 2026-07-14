#!/usr/bin/env python3
"""
Zep memory + DaoXE (OpenAI-compatible chat Completions).

Uses the official OpenAI Python client with base_url pointed at DaoXE.
Model IDs must match your DaoXE account (GET /v1/models). DaoXE is not
available in mainland China.

DaoXE is multi-protocol; this sample only uses the OpenAI-compatible path.
"""

from __future__ import annotations

import os
import uuid

from dotenv import load_dotenv
from openai import OpenAI
from zep_cloud.client import Zep
from zep_cloud.types import Message

load_dotenv()

DAOXE_BASE_URL = os.getenv("DAOXE_BASE_URL", "https://daoxe.com/v1")


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(
            f"Missing {name}. Copy .env.example to .env and set required values."
        )
    return value


def main() -> None:
    zep_api_key = require_env("ZEP_API_KEY")
    daoxe_api_key = require_env("DAOXE_API_KEY")
    model = require_env("DAOXE_MODEL")

    zep = Zep(api_key=zep_api_key)
    llm = OpenAI(api_key=daoxe_api_key, base_url=DAOXE_BASE_URL)

    suffix = uuid.uuid4().hex[:8]
    user_id = f"daoxe-example-user-{suffix}"
    thread_id = f"daoxe-example-thread-{suffix}"
    user_name = "Alex"

    print(f"Creating Zep user={user_id} thread={thread_id}")
    zep.user.add(user_id=user_id, first_name="Alex", last_name="Example")
    zep.thread.create(thread_id=thread_id, user_id=user_id)

    turns = [
        "I prefer concise answers and my favorite color is blue.",
        "What color do I like, and how should you answer me?",
    ]

    for i, user_text in enumerate(turns, start=1):
        print(f"\n=== Turn {i} ===")
        print(f"User: {user_text}")

        zep.thread.add_messages(
            thread_id=thread_id,
            messages=[
                Message(name=user_name, role="user", content=user_text),
            ],
        )

        context_block = ""
        try:
            memory = zep.thread.get_user_context(thread_id=thread_id)
            context_block = memory.context or ""
        except Exception as exc:  # noqa: BLE001 — demo should keep running
            print(f"(Zep context unavailable: {exc})")

        system = (
            "You are a helpful assistant. Use any provided memory context. "
            "Keep replies under 80 words."
        )
        if context_block:
            system = f"{system}\n\n# Memory context\n{context_block}"

        completion = llm.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
            temperature=0.3,
        )
        assistant_text = (completion.choices[0].message.content or "").strip()
        print(f"Assistant: {assistant_text}")

        zep.thread.add_messages(
            thread_id=thread_id,
            messages=[
                Message(
                    name="Assistant",
                    role="assistant",
                    content=assistant_text,
                ),
            ],
        )

    print("\nDone. Inspect the thread in the Zep dashboard if needed.")


if __name__ == "__main__":
    main()
