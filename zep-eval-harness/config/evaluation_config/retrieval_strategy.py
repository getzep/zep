"""
Retrieval strategy for evaluation context blocks.

This module is the source of truth for how the harness retrieves and assembles
context. Edit ``build_context_block`` (and the constants it closes over) to
change search behavior — there is no separate per-scope limit/reranker config.

Default: ``graph.get_context`` with a 10k character budget. The v4 context
endpoint packs edges, nodes, episodes, observations, and thread summaries into
a pre-assembled ``result.context`` string. The user-node summary is fetched
separately (once per user by the eval loop) and prepended — the context
endpoint does not include it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from retry import retry_with_backoff

if TYPE_CHECKING:
    from zep_cloud.client import AsyncZep

STRATEGY_NAME = "graph_context"
MAX_CHARACTERS = 10_000


def get_search_configuration() -> dict:
    """Snapshot of the active retrieval strategy for evaluation result files."""
    return {
        "strategy": STRATEGY_NAME,
        "max_characters": MAX_CHARACTERS,
    }


async def fetch_user_summary(zep_client: AsyncZep, user_uuid: str) -> str | None:
    """Fetch the user-node summary, or None if unavailable.

    Call once per user and pass the result into ``build_context_block`` so
    concurrent queries do not repeat ``user.get_node``.
    """
    try:
        node = await retry_with_backoff(
            zep_client.user.get_node,
            user_uuid,
            description=f"get user node [{user_uuid}]",
        )
        summary = node.summary if node else None
        if summary and summary.strip():
            return summary.strip()
    except Exception as e:
        print(f"  Could not retrieve user summary for [{user_uuid}]: {e}")
    return None


async def build_context_block(
    zep_client: AsyncZep,
    *,
    graph_uuid: str,
    query: str,
    doc_graph_uuid: str | None = None,
    user_summary: str | None = None,
) -> str:
    """
    Retrieve a context block for ``query`` using the configured strategy.

    Gets the context of the user graph, and optionally of a standalone document
    graph in parallel. Prepends ``user_summary`` when provided (fetch it once
    per user via ``fetch_user_summary``).
    """
    print(f"Searching [{graph_uuid}]: '{query}' (max_characters={MAX_CHARACTERS})")

    user_task = retry_with_backoff(
        zep_client.graph.get_context,
        graph_uuid,
        query=query,
        max_characters=MAX_CHARACTERS,
        description=f"get context of user graph [{graph_uuid}]",
    )

    if doc_graph_uuid:
        doc_task = retry_with_backoff(
            zep_client.graph.get_context,
            doc_graph_uuid,
            query=query,
            max_characters=MAX_CHARACTERS,
            description=f"get context of doc graph [{doc_graph_uuid}]",
        )
        user_result, doc_result = await asyncio.gather(user_task, doc_task)
    else:
        user_result = await user_task
        doc_result = None

    parts: list[str] = []

    if user_summary:
        parts.append(
            "# High-level summary of the user\n"
            "<USER_SUMMARY>\n"
            f"{user_summary}\n"
            "</USER_SUMMARY>"
        )

    user_context = user_result.context or ""
    if user_context.strip():
        parts.append(user_context.strip())

    if doc_result is not None:
        doc_context = doc_result.context or ""
        if doc_context.strip():
            parts.append(
                "The following context is from shared reference documents.\n\n"
                + doc_context.strip()
            )

    if not parts:
        return "No relevant context found."

    return "\n\n".join(parts)
