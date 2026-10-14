"""Run a live write and context-read check against Zep."""

import asyncio
import os
import uuid

from zep_cloud.client import AsyncZep

from zep_ag2 import ZepMemoryManager, create_thread, create_user


async def main() -> None:
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])
    suffix = uuid.uuid4().hex[:8]
    user = await create_user(
        zep,
        first_name="AG2 Test",
        email=f"ag2-manual-{suffix}@example.com",
    )
    user_uuid = user.uuid_
    thread = await create_thread(zep, user_uuid=user_uuid)
    thread_uuid = thread.uuid_
    try:
        memory = ZepMemoryManager(
            zep,
            user_uuid=user_uuid,
            thread_uuid=thread_uuid,
            graph_uuid=user_uuid,
        )
        await memory.add_messages(
            [
                {"role": "user", "content": "I work in data science."},
                {"role": "assistant", "content": "I will remember that."},
            ]
        )
        print(await memory.get_memory_context(query="What is my profession?"))
    finally:
        await zep.thread.delete(thread_uuid)
        await zep.user.delete(user_uuid)


if __name__ == "__main__":
    asyncio.run(main())
