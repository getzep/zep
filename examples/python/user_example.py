import asyncio
import os
import sys
import time

from dotenv import find_dotenv, load_dotenv

from zep_cloud.client import AsyncZep
from zep_cloud.core.api_error import ApiError

load_dotenv(
    dotenv_path=find_dotenv()
)  # load environment variables from .env file, if present

API_KEY = os.environ.get("ZEP_API_KEY") or "YOUR_API_KEY"

TERMINAL_TASK_STATUSES = {"succeeded", "partial", "failed"}


async def wait_for_task(
    client: AsyncZep,
    task_uuid: str,
    *,
    timeout_seconds: float = 600.0,
    poll_interval_seconds: float = 1.0,
) -> str | None:
    """Poll a Zep task until it reaches a terminal status."""
    deadline = time.monotonic() + timeout_seconds
    task = await client.task.get(task_uuid)
    while task.status not in TERMINAL_TASK_STATUSES:
        if time.monotonic() > deadline:
            raise TimeoutError(f"task {task_uuid} is still {task.status}")
        await asyncio.sleep(poll_interval_seconds)
        task = await client.task.get(task_uuid)
    return task.status


async def main() -> None:
    client = AsyncZep(api_key=API_KEY)
    # v4 addresses a user by the UUID that Zep returns. Store the UUID in your
    # own database and pass it to every later call.
    created_user_uuids: list[str] = []

    # Create multiple users
    for i in range(3):
        try:
            user = await client.user.create(
                email=f"user{i}@example.com",
                first_name=f"John{i}",
                last_name=f"Doe{i}",
                metadata={"foo": "bar"},
            )
            if user.uuid_ is None:
                raise RuntimeError("user.create did not return a UUID")
            created_user_uuids.append(user.uuid_)
            print(f"Created user {i+1}: {user.uuid_}")
        except ApiError as e:
            print(f"Failed to create user {i+1}: {e}")

    if len(created_user_uuids) < 2:
        raise RuntimeError("Need at least two created users to demonstrate update and delete")

    # Update only a user created in this run
    user_uuid = created_user_uuids[0]
    try:
        updated_user = await client.user.update(
            user_uuid,
            email="updated_user@example.com",
            first_name="UpdatedJohn",
            last_name="UpdatedDoe",
            metadata={"foo": "updated_bar"},
        )
        print(f"Updated user: {updated_user.uuid_}")
    except ApiError as e:
        print(f"Failed to update user: {e}")
        raise

    # Create a Thread for the first created user
    try:
        thread = await client.thread.create(user_uuid=user_uuid)
        print(f"Created thread: {thread.uuid_}")
    except Exception as e:
        print(f"Failed to create thread: {e}")
        raise

    # Delete only a user created in this run. A v4 delete is asynchronous, so
    # the example polls the returned task.
    user_uuid_to_delete = created_user_uuids[1]
    try:
        result = await client.user.delete(user_uuid_to_delete)
        status = await wait_for_task(client, result.task.uuid_)
        print(f"Deleted user: {user_uuid_to_delete} (task {status})")
    except ApiError as e:
        print(f"Failed to delete user: {e}")
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
