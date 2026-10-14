"""Store and retrieve a user memory with Zep."""

import os

from zep_cloud.client import Zep

from zep_crewai import ZepUserStorage, create_search_tool, ensure_thread, ensure_user

zep = Zep(api_key=os.environ["ZEP_API_KEY"])
user, _ = ensure_user(zep, first_name="Alice")
thread, _ = ensure_thread(zep, user_uuid=user.uuid_)

storage = ZepUserStorage(
    client=zep,
    user_uuid=user.uuid_,
    thread_uuid=thread.uuid_,
    graph_uuid=user.uuid_,
)
storage.save("Alice prefers morning meetings.", metadata={"type": "text"})
storage.save(
    "Good morning, Alice.",
    metadata={"type": "message", "role": "assistant"},
)

search_tool = create_search_tool(zep, graph_uuid=user.uuid_)
print(search_tool.run(query="When does Alice prefer meetings?"))
