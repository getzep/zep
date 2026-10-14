"""Fake Zep client and a fixture graph built from data/records.

The fixture mirrors facts.md: the same names, teams, products, and
relationships, so tool-level tests exercise real graph semantics (neighbors,
filters, degree ordering) without a network.
"""

from __future__ import annotations

import json
import sys
import uuid as uuidlib
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATA = Path(__file__).resolve().parent.parent.parent / "agent-with-zep" / "data" / "records"


def _records(name):
    return json.loads((DATA / f"{name}.json").read_text())


class FakeNode:
    def __init__(self, name, labels, attributes=None, summary=""):
        self.uuid_ = str(uuidlib.uuid4())
        self.name = name
        self.labels = labels
        self.attributes = attributes or {}
        self.summary = summary
        self.degree = 0
        self.created_at = None
        self.episodes = []


class FakeEdge:
    def __init__(self, name, fact, source, target):
        self.uuid_ = str(uuidlib.uuid4())
        self.name = name
        self.fact = fact
        self.source_node_uuid = source.uuid_
        self.target_node_uuid = target.uuid_
        self.source_node_name = source.name
        self.target_node_name = target.name
        source.degree += 1
        target.degree += 1


class FakeEpisode:
    def __init__(self, content, metadata=None):
        self.uuid_ = str(uuidlib.uuid4())
        self.content = content
        self.metadata = metadata or {}
        self.processed = True


class FakeNodeClient:
    def __init__(self, graph):
        self._g = graph

    async def list(self, graph_uuid, *, filters=None, order_by=None, page_size=None, **kwargs):
        nodes = list(self._g.nodes.values())
        if filters and filters.node_labels:
            nodes = [n for n in nodes if any(l in filters.node_labels for l in n.labels)]
        if order_by == "degree":
            nodes.sort(key=lambda n: -n.degree)
        self._g.calls.append(
            (
                "node.list",
                {
                    "graph_uuid": graph_uuid,
                    "filters": filters,
                    "order_by": order_by,
                    "page_size": page_size,
                },
            )
        )
        return SimpleNamespace(items=nodes[: page_size or 50])

    async def get(self, graph_uuid, uuid_, request_options=None):
        return self._g.nodes[uuid_]

    async def list_neighbors(
        self, graph_uuid, node_uuid, *, direction=None, filters=None, page_size=None, **kwargs
    ):
        self._g.calls.append(
            (
                "node.list_neighbors",
                {
                    "graph_uuid": graph_uuid,
                    "node_uuid": node_uuid,
                    "filters": filters,
                    "direction": direction,
                    "page_size": page_size,
                },
            )
        )
        edge_types = filters.edge_types if filters else None
        out = []
        for e in self._g.edges.values():
            if edge_types and e.name not in edge_types:
                continue
            if e.source_node_uuid == node_uuid and direction in (None, "both", "out"):
                out.append(SimpleNamespace(node=self._g.nodes[e.target_node_uuid], edges=[e]))
            elif e.target_node_uuid == node_uuid and direction in (None, "both", "in"):
                out.append(SimpleNamespace(node=self._g.nodes[e.source_node_uuid], edges=[e]))
        return SimpleNamespace(items=out[: page_size or 50])


class FakeEpisodeClient:
    def __init__(self, graph):
        self._g = graph

    async def get(self, graph_uuid, uuid_, request_options=None):
        return self._g.episodes[uuid_]

    async def add(self, graph_uuid, **kwargs):
        ep = FakeEpisode(kwargs.get("data", ""), kwargs.get("metadata"))
        self._g.episodes[ep.uuid_] = ep
        return SimpleNamespace(episode=ep, task=None)


class FakeGraph:
    def __init__(self):
        self.nodes = {}
        self.edges = {}
        self.episodes = {}
        self.calls = []
        self.ontology_set = False
        self.node = FakeNodeClient(self)
        self.episode = FakeEpisodeClient(self)

    async def _search(self, name, graph_uuid, **kwargs):
        self.calls.append((name, {"graph_uuid": graph_uuid, **kwargs}))
        return SimpleNamespace(items=[])

    async def search_edges(self, graph_uuid, **kwargs):
        return await self._search("search_edges", graph_uuid, **kwargs)

    async def search_nodes(self, graph_uuid, **kwargs):
        return await self._search("search_nodes", graph_uuid, **kwargs)

    async def search_episodes(self, graph_uuid, **kwargs):
        return await self._search("search_episodes", graph_uuid, **kwargs)

    async def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        return SimpleNamespace(uuid_="created-graph")

    async def get(self, graph_uuid):
        self.calls.append(("get", graph_uuid))

    async def delete(self, graph_uuid, **kw):
        self.calls.append(("delete", graph_uuid))

    async def set_ontology(self, graph_uuid, *, entity_types=None, edge_types=None, **kwargs):
        self.calls.append(
            (
                "set_ontology",
                {
                    "graph_uuid": graph_uuid,
                    "entity_types": entity_types,
                    "edge_types": edge_types,
                },
            )
        )
        self.ontology_set = True


class FakeZep:
    def __init__(self):
        self.graph = FakeGraph()


def build_fixture_graph() -> FakeZep:
    zep = FakeZep()
    g = zep.graph
    by_name = {}

    def node(name, label, attrs=None):
        n = FakeNode(name, ["Entity", label], attrs or {})
        g.nodes[n.uuid_] = n
        by_name[(label, name)] = n
        return n

    def edge(name, src, tgt, fact=""):
        e = FakeEdge(name, fact, src, tgt)
        g.edges[e.uuid_] = e
        return e

    for t in _records("teams"):
        node(t["name"], "Team")
    for s in _records("sites"):
        node(s["name"], "Site", {"country": s["country"]})
    for s in _records("suppliers"):
        node(s["name"], "Supplier")
    for c in _records("components"):
        node(c["part"], "Component", {"part_number": c["part"]})
    for p in _records("products"):
        node(
            p["name"],
            "Product",
            {
                "category": p["category"],
                "lifecycle_status": p["lifecycle_status"],
                "launch_date": p["launch_date"],
            },
        )
    for f in _records("regulatory_filings"):
        node(f["id"], "RegulatoryFiling", dict(f))
    for c in _records("customers"):
        node(c["name"], "Customer", {"segment": c["segment"], "country": c["country"]})
    for q in _records("quality_issues"):
        node(q["issue_id"], "QualityIssue", dict(q))
    for e in _records("employees"):
        node(e["name"], "Employee", {"title": e["title"], "team": e["team"]})

    for p in _records("products"):
        prod = by_name[("Product", p["name"])]
        edge("MANUFACTURED_AT", prod, by_name[("Site", p["site"])])
    for c in _records("components"):
        comp = by_name[("Component", c["part"])]
        edge("SUPPLIES", by_name[("Supplier", c["supplier"])], comp)
        for pname in c["used_in"]:
            edge("CONTAINS", by_name[("Product", pname)], comp)
    for f in _records("regulatory_filings"):
        filing = by_name[("RegulatoryFiling", f["id"])]
        edge("FILED_FOR", filing, by_name[("Product", f["product"])])
        edge("OWNS", by_name[("Employee", f["owner"])], filing)
    for q in _records("quality_issues"):
        qi = by_name[("QualityIssue", q["issue_id"])]
        edge("AFFECTS", qi, by_name[("Product", q["product"])])
        if q.get("component"):
            edge("AFFECTS", qi, by_name[("Component", q["component"])])
        if q.get("owner"):
            edge("OWNS", by_name[("Employee", q["owner"])], qi)
    for e in _records("employees"):
        emp = by_name[("Employee", e["name"])]
        if e["team"]:
            edge("MEMBER_OF", emp, by_name[("Team", e["team"])])
        if e["reports_to"]:
            edge("REPORTS_TO", emp, by_name[("Employee", e["reports_to"])])
        for pname in e["works_on"]:
            edge("WORKS_ON", emp, by_name[("Product", pname)])
    # customer -> REPORTED edges for Aster 410 issues
    edge(
        "REPORTED",
        by_name[("Customer", "Northgate Regional Hospital")],
        by_name[("QualityIssue", "QI-1041")],
    )
    edge(
        "REPORTED",
        by_name[("Customer", "St. Brigid's Hospital")],
        by_name[("QualityIssue", "QI-1041")],
    )
    edge(
        "REPORTED",
        by_name[("Customer", "Harlow Children's Hospital")],
        by_name[("QualityIssue", "QI-1057")],
    )

    # a couple of report episodes
    ep = FakeEpisode(
        "Complaint summary R01, dated 2026-02-03, prepared by Priya Raman.\n\n"
        "Three complaints from Northgate Regional Hospital: Aster 410 pumps "
        "delivered less fluid than programmed. Complaint file opened as QI-1041, "
        "severity high (patient safety).",
        {
            "report_id": "R01",
            "report_type": "complaint_summary",
            "product": "Aster 410",
            "date": "2026-02-03",
            "author": "Priya Raman",
        },
    )
    g.episodes[ep.uuid_] = ep
    return zep


@pytest.fixture
def fake_zep():
    return build_fixture_graph()


@pytest.fixture
def deps(fake_zep):
    from agent_with_zep_adk.tools import AgentDeps

    return AgentDeps(zep=fake_zep, graph_uuid="test-graph")
