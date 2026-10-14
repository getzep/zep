"""Create a user and thread, then read the thread context."""

import os

from zep_cloud.client import Zep

from zep_crewai import ZepUserStorage, ensure_thread, ensure_user

zep = Zep(api_key=os.environ["ZEP_API_KEY"])
user, _ = ensure_user(zep, first_name="Alice")
thread, _ = ensure_thread(zep, user_uuid=user.uuid_)
storage = ZepUserStorage(zep, user_uuid=user.uuid_, thread_uuid=thread.uuid_)

storage.save(
    "I prefer morning meetings.",
    metadata={"type": "message", "role": "user", "name": "Alice"},
)
print(storage.get_context())
