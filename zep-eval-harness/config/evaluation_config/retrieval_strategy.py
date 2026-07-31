"""
Retrieval strategy for evaluation context blocks.

This module is the source of truth for how the harness retrieves and assembles
context. Edit ``build_context_block`` (and the constants it closes over) to
change search behavior — there is no separate per-scope limit/reranker config.

Default: ``scope="auto"`` with a 10k character budget. Auto search packs edges,
nodes, episodes, observations, and thread summaries into a pre-assembled
``result.context`` string. ``limit`` and ``reranker`` do not apply under auto.
The user-node summary is fetched separately (once per user by the eval loop)
and prepended — auto search does not include it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from retry import retry_with_backoff

if TYPE_CHECKING:
    from zep_cloud.client import AsyncZep

STRATEGY_NAME = "auto_search"
SCOPE = "auto"
MAX_CHARACTERS = 10_000


def get_search_configuration() -> dict:
    """Snapshot of the active retrieval strategy for evaluation result files."""
    return {
        "strategy": STRATEGY_NAME,
        "scope": SCOPE,
        "max_characters": MAX_CHARACTERS,
    }


async def fetch_user_summary(zep_client: AsyncZep, user_id: str) -> str | None:
    """Fetch the user-node summary, or None if unavailable.

    Call once per user and pass the result into ``build_context_block`` so
    concurrent queries do not repeat ``user.get_node``.
    """
    try:
        user_node_response = await retry_with_backoff(
            zep_client.user.get_node,
            user_id=user_id,
            description=f"get user node [{user_id}]",
        )
        node = getattr(user_node_response, "node", None)
        summary = getattr(node, "summary", None) if node else None
        if summary and str(summary).strip():
            return str(summary).strip()
    except Exception as e:
        print(f"  Could not retrieve user summary for [{user_id}]: {e}")
    return None


async def build_context_block(
    zep_client: AsyncZep,
    *,
    user_id: str,
    query: str,
    doc_graph_id: str | None = None,
    user_summary: str | None = None,
) -> str:
    """
    Retrieve a context block for ``query`` using the configured strategy.

    Runs auto search on the user graph, and optionally on a standalone document
    graph in parallel. Prepends ``user_summary`` when provided (fetch it once
    per user via ``fetch_user_summary``).
    """
    print(f"Searching [{user_id}]: '{query}' (scope={SCOPE}, max_characters={MAX_CHARACTERS})")

    user_task = retry_with_backoff(
        zep_client.graph.search,
        user_id=user_id,
        query=query,
        scope=SCOPE,
        max_characters=MAX_CHARACTERS,
        description=f"auto search user [{user_id}]",
    )

    if doc_graph_id:
        doc_task = retry_with_backoff(
            zep_client.graph.search,
            graph_id=doc_graph_id,
            query=query,
            scope=SCOPE,
            max_characters=MAX_CHARACTERS,
            description=f"auto search doc [{doc_graph_id}]",
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

    user_context = getattr(user_result, "context", None) or ""
    if user_context.strip():
        parts.append(user_context.strip())

    if doc_result is not None:
        doc_context = getattr(doc_result, "context", None) or ""
        if doc_context.strip():
            parts.append(
                "The following context is from shared reference documents.\n\n"
                + doc_context.strip()
            )

    if not parts:
        return "No relevant context found."

    return "\n\n".join(parts)
