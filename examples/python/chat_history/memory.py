"""
Example of using the Zep Python SDK asynchronously.

This script demonstrates the following functionality:
- Creating a user.
- Creating a thread associated with the created user.
- Adding messages to the thread.
- Searching the thread memory for a specific query.
- Searching the thread memory with MMR reranking.
- optionally deleting the thread.
"""

import asyncio
import os
import uuid

from dotenv import find_dotenv, load_dotenv

from chat_history_shoe_purchase import history

from zep_cloud.client import AsyncZep
from zep_cloud.types import Message, FactRatingInstruction, FactRatingExamples

load_dotenv(
    dotenv_path=find_dotenv()
)  # load environment variables from .env file, if present

API_KEY = os.environ.get("ZEP_API_KEY") or "YOUR_API_KEY"


async def main() -> None:
    client = AsyncZep(
        api_key=API_KEY,
    )

    # Create a user
    user_id = uuid.uuid4().hex  # unique user id. can be any alphanum string
    fact_rating_instruction = """Rate the facts by poignancy. Highly poignant 
    facts have a significant emotional impact or relevance to the user. 
    Facts with low poignancy are minimally relevant or of little emotional
    significance."""
    fact_rating_examples = FactRatingExamples(
        high="The user received news of a family member's serious illness.",
        medium="The user completed a challenging marathon.",
        low="The user bought a new brand of toothpaste.",
    )
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
    for m in history:
        print(f"{m['role']}: {m['content']}")
        await client.thread.add_messages(thread_id=thread_id, messages=[Message(**m)])
        # await asyncio.sleep(0.5)

    #  Wait for the messages to be processed
    await asyncio.sleep(50)


    print(f"\n---Get user context for thread: {thread_id}")
    memory = await client.thread.get_user_context(thread_id)
    print(f"Context: {memory.context}")

    # Delete thread and wipe thread memory
    # Uncomment to run
    # await client.thread.delete(thread_id)


if __name__ == "__main__":
    asyncio.run(main())