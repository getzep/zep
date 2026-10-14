"""Use a custom synchronous context builder."""

import os

from zep_cloud.client import Zep

from zep_crewai import ContextInput, ZepUserStorage, ensure_thread, ensure_user


def build_context(context: ContextInput) -> str | None:
    result = context.zep.graph.search_edges(
        context.graph_uuid,
        query=context.user_message,
        page_size=10,
    )
    facts = [edge.fact for edge in result.items or [] if edge.fact]
    return "\n".join(facts) if facts else None


zep = Zep(api_key=os.environ["ZEP_API_KEY"])
user, _ = ensure_user(zep, first_name="Alice")
thread, _ = ensure_thread(zep, user_uuid=user.uuid_)
storage = ZepUserStorage(
    zep,
    user_uuid=user.uuid_,
    thread_uuid=thread.uuid_,
    context_builder=build_context,
)
print(storage.search("What do you know about Alice?"))
