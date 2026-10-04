"""The six retrieval tools the agent uses against the Zep graph.

All tools share one deps object (the Zep client, the graph id, the handle
registry, the plan list, and the retrieval budget). The application pins the
graph id, reranker, and page-size caps; the model controls queries, filters,
and handles. Each result is marked "ranked sample", "complete", or
"truncated at N" so the model knows whether a set is exhaustive.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Literal

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset
from zep_cloud import AsyncZep
from zep_cloud.types import EpisodeMetadataFilter, MetadataFilterGroup, SearchFilters

from . import ontology
from .config import (
    EPISODE_TEXT_MAX_CHARS,
    LIST_LIMIT_MAX,
    MAX_RESULT_CHARS,
    MAX_TOOL_CALLS,
    NEIGHBOR_LIMIT_MAX,
    SEARCH_LIMIT_DEFAULT,
    SEARCH_LIMIT_MAX,
)
from .handles import HandleRegistry
from .planner import RetrievalPlan, add_submit_plan

Scope = Literal["edges", "nodes", "episodes"]
EntityLabel = Literal[tuple(ontology.ENTITY_LABELS)]  # type: ignore[valid-type]
EdgeTypeName = Literal[tuple(ontology.EDGE_TYPE_NAMES)]  # type: ignore[valid-type]


@dataclass
class AgentDeps:
    """Per-run shared state for the retrieval tools and the planner."""

    zep: AsyncZep
    graph_id: str
    registry: HandleRegistry = field(default_factory=HandleRegistry)
    plans: list[RetrievalPlan] = field(default_factory=list)
    calls_left: int = MAX_TOOL_CALLS
    call_log: list[dict] = field(default_factory=list)
    _seen_calls: set[str] = field(default_factory=set)

    def gate(self, tool_name: str, args: dict) -> str | None:
        """Apply the call budget and dedup. Returns a notice or None."""
        key = tool_name + json.dumps(args, sort_keys=True, default=str)
        if key in self._seen_calls:
            return "duplicate call: you already have this result. Do not repeat a call with the same arguments."
        if self.calls_left <= 0:
            return f"budget exhausted: you have used all {MAX_TOOL_CALLS} retrieval calls. Answer from the evidence you have."
        self.calls_left -= 1
        self._seen_calls.add(key)
        return None

    def log_call(self, tool_name: str, args: dict, result: str, ms: int) -> None:
        self.call_log.append({"name": tool_name, "args": args, "result": result, "ms": ms})


def _seen_mark(deps: AgentDeps, handle: str) -> str:
    mark = " (seen)" if deps.registry.is_seen(handle) else ""
    deps.registry.mark_seen(handle)
    return mark


def _node_line(deps: AgentDeps, node) -> str:
    handle = deps.registry.register(node.uuid_, "n")
    labels = ",".join(node.labels or [])
    attrs = "; ".join(
        f"{k}={v}"
        for k, v in (node.attributes or {}).items()
        if v not in (None, "") and k not in ("labels", "name", "uuid") and not k.startswith("_")
    )
    summary = (node.summary or "")[:200]
    return f"- {handle} {node.name} [{labels}] {attrs} {summary}{_seen_mark(deps, handle)}"


def _resolve_or_notice(deps: AgentDeps, handle: str, prefix: str) -> str | None:
    """Resolve a handle to a UUID or return None."""
    try:
        return deps.registry.resolve(handle, prefix=prefix)
    except KeyError:
        return None


def _edge_line(deps: AgentDeps, edge) -> str:
    handle = deps.registry.register(edge.uuid_, "e")
    src = deps.registry.register(edge.source_node_uuid, "n")
    tgt = deps.registry.register(edge.target_node_uuid, "n")
    deps.registry.mark_seen(edge.source_node_uuid)
    deps.registry.mark_seen(edge.target_node_uuid)
    return (
        f"- {handle} {edge.name} {src} {edge.source_node_name} -> {tgt} {edge.target_node_name}: "
        f"{edge.fact or ''}{_seen_mark(deps, handle)}"
    )


def _episode_line(deps: AgentDeps, episode) -> str:
    handle = deps.registry.register(episode.uuid_, "p")
    meta = episode.metadata or {}
    title = meta.get("report_id") or meta.get("title") or ""
    snippet = (episode.content or "")[:200].replace("\n", " ")
    return f"- {handle} {title} ({meta.get('report_type', '')}, {meta.get('date', '')}): {snippet}{_seen_mark(deps, handle)}"


def _finish(
    deps: AgentDeps, tool_name: str, args: dict, header: str, lines: list[str], start: float
) -> str:
    text = header + "\n" + ("\n".join(lines) if lines else "- no matches")
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "\n[truncated]"
    deps.log_call(tool_name, args, text, int((time.monotonic() - start) * 1000))
    return text


def build_toolset() -> FunctionToolset:
    """Create the toolset with all retrieval tools and submit_plan."""
    toolset = FunctionToolset()

    @toolset.tool
    async def search_context(
        ctx: RunContext[AgentDeps],
        query: str,
        scope: Scope = "edges",
        node_labels: list[EntityLabel] | None = None,
        edge_types: list[EdgeTypeName] | None = None,
        report_type: str | None = None,
        product: str | None = None,
        near: list[str] | None = None,
        limit: int = SEARCH_LIMIT_DEFAULT,
    ) -> str:
        """Search the graph for the items most relevant to a query. Returns a ranked sample, not a complete set.

        Use scope="edges" for facts and relationships, scope="nodes" for entities, and scope="episodes" for the source reports. Use report_type and product to restrict the search to reports with that metadata. Use near with one or more handles to search only the graph neighborhood of known nodes."""
        args = {
            "query": query,
            "scope": scope,
            "node_labels": node_labels,
            "edge_types": edge_types,
            "report_type": report_type,
            "product": product,
            "near": near,
            "limit": limit,
        }
        bfs = None
        if near:
            bfs = []
            for h in near:
                uuid = _resolve_or_notice(ctx.deps, h, "n")
                if uuid is None:
                    return f"unknown handle {h}: pass an n* handle from a previous result."
                bfs.append(uuid)
        notice = ctx.deps.gate("search_context", args)
        if notice:
            return notice
        start = time.monotonic()
        meta_filters = []
        if report_type:
            meta_filters.append(
                EpisodeMetadataFilter(
                    comparison_operator="=", property_name="report_type", property_value=report_type
                )
            )
        if product:
            meta_filters.append(
                EpisodeMetadataFilter(
                    comparison_operator="=", property_name="product", property_value=product
                )
            )
        filters = SearchFilters(
            node_labels=list(node_labels) if node_labels else None,
            edge_types=list(edge_types) if edge_types else None,
            episode_metadata_filters=MetadataFilterGroup(type="and", filters=meta_filters)
            if meta_filters
            else None,
        )
        results = await ctx.deps.zep.graph.search(
            graph_id=ctx.deps.graph_id,
            query=query,
            scope=scope,
            limit=min(limit, SEARCH_LIMIT_MAX),
            reranker="cross_encoder",
            bfs_origin_node_uuids=bfs,
            search_filters=filters,
        )
        lines: list[str] = []
        for edge in results.edges or []:
            lines.append(_edge_line(ctx.deps, edge))
        for node in results.nodes or []:
            lines.append(_node_line(ctx.deps, node))
        for episode in results.episodes or []:
            lines.append(_episode_line(ctx.deps, episode))
        return _finish(ctx.deps, "search_context", args, "ranked sample:", lines, start)

    @toolset.tool
    async def list_nodes(
        ctx: RunContext[AgentDeps],
        label: EntityLabel,
        order_by: Literal["degree"] | None = None,
        limit: int = LIST_LIMIT_MAX,
    ) -> str:
        """List the nodes that have one entity type. Returns every match up to the limit, and says if the list is complete.

        Use this when the question needs a complete set, such as all open quality issues or all products. Set order_by="degree" to list the most connected nodes first."""
        args = {"label": label, "order_by": order_by, "limit": limit}
        notice = ctx.deps.gate("list_nodes", args)
        if notice:
            return notice
        start = time.monotonic()
        page_size = min(limit, LIST_LIMIT_MAX)
        nodes = await ctx.deps.zep.graph.node.get_by_graph_id(
            ctx.deps.graph_id,
            filters=SearchFilters(node_labels=[label]),
            order_by=order_by,
            limit=page_size + 1,
        )
        nodes = list(nodes or [])
        truncated = len(nodes) > page_size
        nodes = nodes[:page_size]
        marker = f"truncated at {page_size}" if truncated else "complete"
        lines = [_node_line(ctx.deps, n) for n in nodes]
        return _finish(ctx.deps, "list_nodes", args, f"{marker}:", lines, start)

    @toolset.tool
    async def get_neighborhood(
        ctx: RunContext[AgentDeps],
        handle: str,
        edge_types: list[EdgeTypeName] | None = None,
        direction: Literal["out", "in", "both"] = "both",
        limit: int = NEIGHBOR_LIMIT_MAX,
    ) -> str:
        """Get the edges and neighbor nodes of one node. Returns every match up to the limit, and says if the list is complete.

        Use this to follow relationships from a node you already have, such as the components of a product, the supplier of a component, or the manager of an employee. Use edge_types to keep only some relationships. Use direction="out" for edges that start at the node, such as the manager of an employee (REPORTS_TO). Use direction="in" for edges that end at the node, such as the members of a team (MEMBER_OF)."""
        args = {"handle": handle, "edge_types": edge_types, "direction": direction, "limit": limit}
        node_uuid = _resolve_or_notice(ctx.deps, handle, "n")
        if node_uuid is None:
            return f"unknown handle {handle}: pass an n* handle from a previous result."
        notice = ctx.deps.gate("get_neighborhood", args)
        if notice:
            return notice
        start = time.monotonic()
        page_size = min(limit, NEIGHBOR_LIMIT_MAX)
        neighbors = await ctx.deps.zep.graph.node.get_neighbors(
            node_uuid,
            filters=SearchFilters(edge_types=list(edge_types)) if edge_types else None,
            direction=direction,
            limit=page_size + 1,
        )
        neighbors = list(neighbors or [])
        truncated = len(neighbors) > page_size
        neighbors = neighbors[:page_size]
        marker = f"truncated at {page_size}" if truncated else "complete"
        lines: list[str] = []
        for neighbor in neighbors:
            for edge in neighbor.edges or []:
                lines.append(_edge_line(ctx.deps, edge))
            if neighbor.node is not None:
                lines.append(_node_line(ctx.deps, neighbor.node))
        return _finish(ctx.deps, "get_neighborhood", args, f"{marker}:", lines, start)

    @toolset.tool
    async def get_details(ctx: RunContext[AgentDeps], handle: str) -> str:
        """Get the full record for one handle: the attributes and summary of a node, or the full text and metadata of a report (episode).

        Use this when a search result is relevant but too short to answer from, for example to read a full report."""
        args = {"handle": handle}
        if handle.startswith("p"):
            uuid = _resolve_or_notice(ctx.deps, handle, "p")
            if uuid is None:
                return f"unknown handle {handle}: pass an n* or p* handle from a previous result."
            notice = ctx.deps.gate("get_details", args)
            if notice:
                return notice
            start = time.monotonic()
            episode = await ctx.deps.zep.graph.episode.get(uuid)
            content = episode.content or ""
            if len(content) > EPISODE_TEXT_MAX_CHARS:
                content = content[:EPISODE_TEXT_MAX_CHARS] + "\n[truncated]"
            meta = json.dumps(episode.metadata or {}, ensure_ascii=False)
            text = f"- {handle} metadata: {meta}\n{content}{_seen_mark(ctx.deps, handle)}"
        elif handle.startswith("n"):
            uuid = _resolve_or_notice(ctx.deps, handle, "n")
            if uuid is None:
                return f"unknown handle {handle}: pass an n* or p* handle from a previous result."
            notice = ctx.deps.gate("get_details", args)
            if notice:
                return notice
            start = time.monotonic()
            node = await ctx.deps.zep.graph.node.get(uuid)
            attrs = json.dumps(node.attributes or {}, ensure_ascii=False)
            text = (
                f"- {handle} {node.name} [{','.join(node.labels or [])}]\n"
                f"attributes: {attrs}\nsummary: {node.summary or ''}{_seen_mark(ctx.deps, handle)}"
            )
        else:
            return f"unknown handle {handle}: pass an n* or p* handle from a previous result."
        return _finish(ctx.deps, "get_details", args, "details:", [text], start)

    @toolset.tool
    async def get_employees(
        ctx: RunContext[AgentDeps],
        team: str | None = None,
        product: str | None = None,
    ) -> str:
        """Find employees by team, by the product they work on, or both. Returns every match, with each person's title, team, and manager.

        Use this for questions about who works on something or who is on a team. Names must match the graph, such as "Reliability Engineering" or "Aster 410"."""
        args = {"team": team, "product": product}
        if team is None and product is None:
            return "pass team, product, or both."
        notice = ctx.deps.gate("get_employees", args)
        if notice:
            return notice
        start = time.monotonic()

        async def node_by_name(label: str, name: str):
            nodes = await ctx.deps.zep.graph.node.get_by_graph_id(
                ctx.deps.graph_id,
                filters=SearchFilters(node_labels=[label]),
                limit=LIST_LIMIT_MAX,
            )
            for n in nodes or []:
                if (n.name or "").strip().casefold() == name.strip().casefold():
                    return n
            return None

        async def member_uuids(node_uuid: str, edge_type: str) -> dict[str, object]:
            neighbors = await ctx.deps.zep.graph.node.get_neighbors(
                node_uuid,
                filters=SearchFilters(edge_types=[edge_type]),
                direction="in",
                limit=NEIGHBOR_LIMIT_MAX,
            )
            out = {}
            for nb in neighbors or []:
                if nb.node is not None and "Employee" in (nb.node.labels or []):
                    out[nb.node.uuid_] = nb.node
            return out

        sets: list[dict[str, object]] = []
        if team is not None:
            node = await node_by_name("Team", team)
            if node is None:
                return f"no Team node named {team!r}."
            sets.append(await member_uuids(node.uuid_, "MEMBER_OF"))
        if product is not None:
            node = await node_by_name("Product", product)
            if node is None:
                return f"no Product node named {product!r}."
            sets.append(await member_uuids(node.uuid_, "WORKS_ON"))
        employees = sets[0]
        for other in sets[1:]:
            employees = {u: n for u, n in employees.items() if u in other}

        lines = []
        for uuid, node in employees.items():
            manager = ""
            neighbors = await ctx.deps.zep.graph.node.get_neighbors(
                uuid,
                filters=SearchFilters(edge_types=["REPORTS_TO"]),
                direction="out",
                limit=5,
            )
            for nb in neighbors or []:
                if nb.node is not None:
                    manager = f"; manager: {nb.node.name}"
            handle = ctx.deps.registry.register(uuid, "n")
            attrs = node.attributes or {}
            lines.append(
                f"- {handle} {node.name} ({attrs.get('title', '')}; team: {attrs.get('team', '')}{manager}){_seen_mark(ctx.deps, handle)}"
            )
        return _finish(ctx.deps, "get_employees", args, "complete:", lines, start)

    @toolset.tool
    async def search_products(
        ctx: RunContext[AgentDeps],
        query: str | None = None,
        category: str | None = None,
    ) -> str:
        """Get products with their category, lifecycle status, and every regulatory filing (region, status, and key dates). Returns every product that matches.

        Use this for questions about which products are cleared, launched, or in a category. Read each filing status: only a cleared 510(k) or a valid CE certificate clears a product in a region."""
        args = {"query": query, "category": category}
        notice = ctx.deps.gate("search_products", args)
        if notice:
            return notice
        start = time.monotonic()
        products = await ctx.deps.zep.graph.node.get_by_graph_id(
            ctx.deps.graph_id,
            filters=SearchFilters(node_labels=["Product"]),
            limit=LIST_LIMIT_MAX,
        )
        lines = []
        for product in products or []:
            attrs = product.attributes or {}
            if (
                query
                and query.lower() not in (product.name or "").lower()
                and query.lower() not in str(attrs.get("category", "")).lower()
            ):
                continue
            if category and category.lower() not in str(attrs.get("category", "")).lower():
                continue
            handle = ctx.deps.registry.register(product.uuid_, "n")
            filings = []
            neighbors = await ctx.deps.zep.graph.node.get_neighbors(
                product.uuid_,
                filters=SearchFilters(edge_types=["FILED_FOR"]),
                limit=NEIGHBOR_LIMIT_MAX,
            )
            for nb in neighbors or []:
                n = nb.node
                if n is None or "RegulatoryFiling" not in (n.labels or []):
                    continue
                fa = n.attributes or {}
                fh = ctx.deps.registry.register(n.uuid_, "n")
                filings.append(
                    f"    - {fh} {n.name}: region={fa.get('region')} status={fa.get('status')} key_date={fa.get('key_date')}"
                )
                ctx.deps.registry.mark_seen(n.uuid_)
            lines.append(
                f"- {handle} {product.name}: category={attrs.get('category')} "
                f"lifecycle_status={attrs.get('lifecycle_status')} launch_date={attrs.get('launch_date')}"
                f"{_seen_mark(ctx.deps, handle)}"
            )
            lines.extend(filings)
        return _finish(ctx.deps, "search_products", args, "complete:", lines, start)

    add_submit_plan(toolset)
    return toolset
