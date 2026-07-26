"""
Retrieval Tools

The tools the response model can call to retrieve context from Zep when tool
mode is on (`USE_TOOLS` in constants.py, or `--tools`). Everything about the
tool set is defined here: which tools exist, what the model is told they do,
what arguments they accept, and how their results are rendered.

To add a tool: write an async executor taking `ctx: ToolContext` plus the
arguments you declare, then append a `ToolSpec` to `TOOL_SPECS`. To retire one
without deleting it, set `enabled=False`. Tools that only make sense with a
document graph get `requires_doc_graph=True` — they are skipped automatically
on runs without `--doc-run`.

Every tool's output is recorded and fed to the context completeness judge, so
tool output should read like context, not like an API dump.
"""

from config.evaluation_config.constants import (
    SEARCH_MAX_QUERY_CHARS,
    TOOL_SEARCH_DEFAULT_LIMIT,
    TOOL_SEARCH_MAX_CHARACTERS,
    TOOL_SEARCH_MAX_LIMIT,
    TOOL_SEARCH_RERANKER,
)
from config.evaluation_config.formatting import (
    format_edges,
    format_episodes,
    format_nodes,
)
from retry import retry_with_backoff
from tool_agent import ToolContext, ToolSpec

TOOL_SEARCH_SCOPES = ["auto", "edges", "nodes", "episodes"]


# ============================================================================
# Rendering
# ============================================================================


FACT_VALIDITY_NOTE = (
    '# Facts ending in "present" are currently valid. '
    "Facts with a past end date are NO LONGER VALID."
)


def _render_search_results(results, scope: str) -> str:
    """Render Zep search results as context text for the model."""
    # scope="auto" spans all context types and returns a pre-assembled block. The
    # validity note is prepended here too, so a dated fact reads the same however
    # it was retrieved — otherwise the default tool scope would be the one path
    # that never tells the model a past end date means the fact no longer holds.
    context = getattr(results, "context", None)
    if scope == "auto" and context:
        return f"{FACT_VALIDITY_NOTE}\n{context}"

    sections: list[str] = []
    for label, items, formatter in (
        ("FACTS", getattr(results, "edges", None), format_edges),
        ("ENTITIES", getattr(results, "nodes", None), format_nodes),
        ("EPISODES", getattr(results, "episodes", None), format_episodes),
        ("OBSERVATIONS", getattr(results, "observations", None), format_nodes),
        ("THREAD SUMMARIES", getattr(results, "thread_summaries", None), format_nodes),
    ):
        if items:
            sections.append(f"# {label}")
            # Same temporal-validity guidance the context block carries, so
            # dated facts read identically in both retrieval modes.
            if label == "FACTS":
                sections.append(FACT_VALIDITY_NOTE)
            sections.extend(formatter(items))

    if not sections:
        return "No results found for this query."
    return "\n".join(sections).strip()


async def _search(
    ctx: ToolContext,
    *,
    query: str,
    scope: str,
    limit: int | None,
    label: str,
    **target,
) -> str:
    """Run one graph search against the given target (user_id or graph_id)."""
    if scope not in TOOL_SEARCH_SCOPES:
        # Raised, not returned, so it lands in the run's tool error count. The
        # model still sees the message and can reissue the call.
        raise ValueError(
            f"invalid scope '{scope}' — valid scopes: {', '.join(TOOL_SEARCH_SCOPES)}"
        )

    # Zep honours different knobs per scope: max_characters bounds scope="auto"
    # (which ignores limit and always retrieves with RRF), while limit and
    # reranker bound every other scope. Sending only what applies keeps the
    # recorded configuration honest.
    if scope == "auto":
        bounds = {"max_characters": TOOL_SEARCH_MAX_CHARACTERS}
    else:
        # Clamped at both ends: the limit comes from the model, and the API
        # rejects out-of-range values, which would cost a budget slot and be
        # recorded as a retrieval failure.
        requested = int(limit) if limit is not None else TOOL_SEARCH_DEFAULT_LIMIT
        bounds = {
            "limit": max(1, min(requested, TOOL_SEARCH_MAX_LIMIT)),
            "reranker": TOOL_SEARCH_RERANKER,
        }

    results = await retry_with_backoff(
        ctx.zep_client.graph.search,
        # Truncated rather than rejected: the API caps query length, and a long
        # model-written query still retrieves usefully from its first 400 chars.
        query=query[:SEARCH_MAX_QUERY_CHARS],
        scope=scope,
        description=f"tool search {label} [{query[:40]}]",
        **bounds,
        **target,
    )
    return _render_search_results(results, scope)


# ============================================================================
# Executors
# ============================================================================


async def search_memory(
    ctx: ToolContext,
    query: str,
    scope: str = "auto",
    limit: int | None = None,
) -> str:
    """Search the user's own knowledge graph."""
    return await _search(
        ctx,
        query=query,
        scope=scope,
        limit=limit,
        label=f"user [{ctx.user_id}]",
        user_id=ctx.user_id,
    )


async def search_documents(
    ctx: ToolContext,
    query: str,
    scope: str = "auto",
    limit: int | None = None,
) -> str:
    """Search the shared reference-document graph."""
    if not ctx.doc_graph_id:
        return "No reference document graph is available for this run."
    return await _search(
        ctx,
        query=query,
        scope=scope,
        limit=limit,
        label=f"doc [{ctx.doc_graph_id}]",
        graph_id=ctx.doc_graph_id,
    )


async def get_user_profile(ctx: ToolContext) -> str:
    """Return the user node's summary — a high-level profile of the user."""
    if not ctx.user_summary:
        return "No user profile summary is available for this user."
    return f"# High-level summary of the user\n{ctx.user_summary}"


# ============================================================================
# Tool set
# ============================================================================

_SEARCH_PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "maxLength": SEARCH_MAX_QUERY_CHARS,
            "description": (
                "What to look for, phrased as a natural-language statement or "
                "question. Be specific — the query is matched semantically. "
                f"Keep it under {SEARCH_MAX_QUERY_CHARS} characters; issue "
                "separate calls rather than one long multi-part query."
            ),
        },
        "scope": {
            "type": "string",
            "enum": TOOL_SEARCH_SCOPES,
            "description": (
                "What to retrieve. 'auto' (default, recommended) spans all "
                "context types. 'edges' returns facts about relationships "
                "between entities, 'nodes' returns entity summaries, "
                "'episodes' returns the raw source messages and documents."
            ),
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": TOOL_SEARCH_MAX_LIMIT,
            "description": (
                "Max results to return, for the 'edges', 'nodes' and 'episodes' "
                f"scopes only (default {TOOL_SEARCH_DEFAULT_LIMIT}, max "
                f"{TOOL_SEARCH_MAX_LIMIT}). Ignored for scope 'auto', which is "
                "bounded by a character budget instead."
            ),
        },
    },
    "required": ["query"],
}

TOOL_SPECS = [
    ToolSpec(
        name="search_memory",
        description=(
            "Search everything remembered about this user: their conversation "
            "history, stated preferences, activity, and the facts and entities "
            "derived from all of it. Use this for any question about the user, "
            "their situation, or anything they have said or done. Issue several "
            "calls in parallel with different queries when a question has "
            "multiple parts."
        ),
        parameters=_SEARCH_PARAMETERS,
        executor=search_memory,
    ),
    ToolSpec(
        name="search_documents",
        description=(
            "Search the shared reference documents — general, user-agnostic "
            "material such as guides and reference material. Use this for "
            "questions about how something works in general, rather than "
            "questions about this specific user."
        ),
        parameters=_SEARCH_PARAMETERS,
        executor=search_documents,
        requires_doc_graph=True,
    ),
    ToolSpec(
        name="get_user_profile",
        description=(
            "Get a high-level summary of who this user is. Useful for broad "
            "orientation before targeted searches, or for questions about the "
            "user's overall situation. Takes no arguments."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        executor=get_user_profile,
    ),
]
