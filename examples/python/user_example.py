import asyncio
import os
import sys
import uuid

from dotenv import find_dotenv, load_dotenv

from zep_cloud.client import AsyncZep
from zep_cloud.core.api_error import ApiError

load_dotenv(
    dotenv_path=find_dotenv()
)  # load environment variables from .env file, if present

API_KEY = os.environ.get("ZEP_API_KEY") or "YOUR_API_KEY"


async def main() -> None:
    client = AsyncZep(api_key=API_KEY)
    created_user_ids: list[str] = []

    # Create multiple users
    for i in range(3):
        user_id = f"user{i}" + uuid.uuid4().hex
        try:
            user = await client.user.add(
                user_id=user_id,
                email=f"user{i}@example.com",
                first_name=f"John{i}",
                last_name=f"Doe{i}",
                metadata={"foo": "bar"},
            )
            created_user_ids.append(user.user_id or user_id)
            print(f"Created user {i+1}: {user.user_id}")
        except ApiError as e:
            print(f"Failed to create user {i+1}: {e}")

    if len(created_user_ids) < 2:
        raise RuntimeError("Need at least two created users to demonstrate update and delete")

    # Update only a user created in this run
    user_id = created_user_ids[0]
    try:
        updated_user = await client.user.update(
            user_id=user_id,
            email="updated_user@example.com",
            first_name="UpdatedJohn",
            last_name="UpdatedDoe",
            metadata={"foo": "updated_bar"},
        )
        print(f"Updated user: {updated_user.user_id}")
    except ApiError as e:
        print(f"Failed to update user: {e}")
        raise

    # Create a Thread for the first created user
    thread_id = uuid.uuid4().hex
    try:
        result = await client.thread.create(
            thread_id=thread_id, user_id=user_id
        )
        print(f"Created session: {result}")
    except Exception as e:
        print(f"Failed to create session: {e}")
        raise

    # Delete only a user created in this run
    user_id_to_delete = created_user_ids[1]
    try:
        await client.user.delete(user_id=user_id_to_delete)
        print(f"Deleted user: {user_id_to_delete}")
    except ApiError as e:
        print(f"Failed to delete user: {e}")
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
