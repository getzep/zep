"""
Example of using the Zep Python SDK asynchronously.

This script demonstrates the following functionality:
- Creating a user.
- Creating a thread associated with the created user.
- Adding messages to the thread.
- Retrieving synthesized user context with thread.get_user_context.
- optionally deleting the thread.
"""

import asyncio
import os
import time
import uuid

from dotenv import find_dotenv, load_dotenv

from chat_history_shoe_purchase import history

from zep_cloud.client import AsyncZep
from zep_cloud.types import Message

TASK_SUCCESS_STATUSES = {"succeeded", "completed", "complete", "success"}
TASK_FAILURE_STATUSES = {"failed", "error", "canceled", "cancelled", "partial"}

load_dotenv(
    dotenv_path=find_dotenv()
)  # load environment variables from .env file, if present

API_KEY = os.environ.get("ZEP_API_KEY") or "YOUR_API_KEY"


async def wait_for_task(
    client: AsyncZep,
    task_id: str | None,
    *,
    timeout_seconds: float = 180.0,
    poll_interval_seconds: float = 2.0,
) -> None:
    """Poll Zep task status until complete, with a bounded timeout."""
    if not task_id:
        return

    deadline = time.monotonic() + timeout_seconds
    while True:
        task = await client.task.get(task_id)
        status = (task.status or "").lower()
        if status in TASK_SUCCESS_STATUSES:
            return
        if status in TASK_FAILURE_STATUSES:
            raise RuntimeError(f"task {task_id} ended with status={status}: {task.error}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for task {task_id} after {timeout_seconds}s")
        await asyncio.sleep(poll_interval_seconds)


async def main() -> None:
    client = AsyncZep(
        api_key=API_KEY,
    )

    # Create a user
    user_id = uuid.uuid4().hex  # unique user id. can be any alphanum string
    await client.user.add(
        user_id=user_id,
        email="user@example.com",
        first_name="Jane",
        last_name="Smith",
    )

    print(f"User added: {user_id}")
    thread_id = uuid.uuid4().hex  # unique thread id. can be any alphanum string

    # Create thread associated with the above user
    print(f"\n---Creating thread: {thread_id}")

    await client.thread.create(
        thread_id=thread_id,
        user_id=user_id,
    )

    print(f"\n---Getting thread: {thread_id}")
    thread = await client.thread.get(thread_id)
    print(f"thread details: {thread}")

    print(f"\n---Add messages to the thread: {thread_id}")
    last_task_id = None
    for m in history:
        print(f"{m['role']}: {m['content']}")
        response = await client.thread.add_messages(
            thread_id=thread_id, messages=[Message(**m)]
        )
        last_task_id = response.task_id or last_task_id

    await wait_for_task(client, last_task_id)

    print(f"\n---Get user context for thread: {thread_id}")
    memory = await client.thread.get_user_context(thread_id)
    print(f"Context: {memory.context}")

    # Delete thread and wipe thread memory
    # Uncomment to run
    # await client.thread.delete(thread_id)


if __name__ == "__main__":
    asyncio.run(main())
