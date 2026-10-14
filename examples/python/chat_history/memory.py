"""
Example of using the Zep Python SDK asynchronously.

This script demonstrates the following functionality:
- Creating a user.
- Creating a thread associated with the created user.
- Adding messages to the thread.
- Retrieving synthesized user context with thread.get_context.
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
    user = await client.user.create(
        user_id=f"examples-v4-chat-history-{uuid.uuid4().hex}",
        email="user@example.com",
        first_name="Jane",
        last_name="Smith",
    )

    user_uuid = user.uuid_
    print(f"User created: {user_uuid}")

    # Create thread associated with the above user
    thread = await client.thread.create(user_uuid=user_uuid)
    thread_uuid = thread.uuid_
    print(f"\n---Created thread: {thread_uuid}")

    print(f"\n---Getting thread: {thread_uuid}")
    thread = await client.thread.get(thread_uuid)
    print(f"thread details: {thread}")

    print(f"\n---Add messages to the thread: {thread_uuid}")
    for message in history:
        print(f"{message['role']}: {message['content']}")
    response = await client.thread.add_messages(
        thread_uuid=thread_uuid,
        messages=[Message(**message) for message in history],
    )
    last_task_id = response.task.uuid_ if response.task else None

    await wait_for_task(client, last_task_id)

    print(f"\n---Get user context for thread: {thread_uuid}")
    memory = await client.thread.get_context(thread_uuid)
    print(f"Context: {memory.context}")

    # Delete thread and wipe thread memory
    # Uncomment to run
    # await client.thread.delete(thread_uuid)


if __name__ == "__main__":
    asyncio.run(main())
