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

DATA = Path(__file__).resolve().parent.parent / "data" / "records"


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

    async def get_by_graph_id(
        self,
        graph_id,
        cursor=None,
        direction=None,
        filters=None,
        limit=None,
        order_by=None,
        uuid_cursor=None,
        request_options=None,
    ):
        nodes = list(self._g.nodes.values())
        if filters and filters.node_labels:
            nodes = [n for n in nodes if any(l in filters.node_labels for l in n.labels)]
        if order_by == "degree":
            nodes.sort(key=lambda n: -n.degree)
        self._g.calls.append(
            ("get_by_graph_id", {"filters": filters, "order_by": order_by, "limit": limit})
        )
        return nodes[: limit or 50]

    async def get(self, uuid_, request_options=None):
        return self._g.nodes[uuid_]

    async def get_neighbors(
        self,
        node_uuid,
        cursor=None,
        direction=None,
        direction_sort=None,
        filters=None,
        limit=None,
        order_by=None,
        request_options=None,
    ):
        self._g.calls.append(
            ("get_neighbors", {"node_uuid": node_uuid, "filters": filters, "limit": limit})
        )
        edge_types = filters.edge_types if filters else None
        out = []
        for e in self._g.edges.values():
            if edge_types and e.name not in edge_types:
                continue
            if e.source_node_uuid == node_uuid:
                out.append(SimpleNamespace(node=self._g.nodes[e.target_node_uuid], edges=[e]))
            elif e.target_node_uuid == node_uuid:
                out.append(SimpleNamespace(node=self._g.nodes[e.source_node_uuid], edges=[e]))
        return out[: limit or 30]


class FakeEpisodeClient:
    def __init__(self, graph):
        self._g = graph

    async def get(self, uuid_, request_options=None):
        return self._g.episodes[uuid_]


class FakeGraph:
    def __init__(self):
        self.nodes = {}
        self.edges = {}
        self.episodes = {}
        self.calls = []
        self.ontology_set = False
        self.node = FakeNodeClient(self)
        self.episode = FakeEpisodeClient(self)

    async def search(self, **kwargs):
        self.calls.append(("search", kwargs))
        return SimpleNamespace(edges=[], nodes=[], episodes=[])

    async def create(self, graph_id, **kw):
        self.calls.append(("create", graph_id))

    async def delete(self, graph_id, **kw):
        self.calls.append(("delete", graph_id))

    async def set_ontology(self, entities, edges=None, graph_ids=None, **kw):
        self.ontology_set = True

    async def add(self, **kw):
        ep = FakeEpisode(kw.get("data", ""), kw.get("metadata"))
        self.episodes[ep.uuid_] = ep
        return ep


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
    from agent_with_zep.tools import AgentDeps

    return AgentDeps(zep=fake_zep, graph_id="test-graph")
