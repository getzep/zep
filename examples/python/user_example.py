import asyncio
import os
import uuid

from dotenv import find_dotenv, load_dotenv

from zep_cloud.types import ApiError
from zep_cloud.client import AsyncZep

load_dotenv(
    dotenv_path=find_dotenv()
)  # load environment variables from .env file, if present

API_KEY = os.environ.get("ZEP_API_KEY") or "YOUR_API_KEY"


async def main() -> None:
    client = AsyncZep(api_key=API_KEY)
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
            print(f"Created user {i+1}: {user.user_id}")
        except ApiError as e:
            print(f"Failed to create user {i+1}: {e}")

    # Update the first user
    user_list = await client.user.list_ordered(page_size=1, page_number=1)
    user_id = user_list.users[0].user_id
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

    # Create a Thread for the first user
    thread_id = uuid.uuid4().hex
    try:
        result = await client.thread.create(
            thread_id=thread_id, user_id=user_id
        )
        print(f"Created session {i+1}: {result}")
    except Exception as e:
        print(f"Failed to create session {i+1}: {e}")

    # Delete the second user
    user_list = await client.user.list_ordered(page_size=1, page_number=1)
    user_id = user_list.users[0].user_id
    try:
        await client.user.delete(user_id=user_id)
        print(f"Deleted user: {user_id}")
    except ApiError as e:
        print(f"Failed to delete user: {e}")


if __name__ == "__main__":
    asyncio.run(main())