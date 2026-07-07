"""Mold real structured JSON into fact triples and seed a graph with them.

Real exports don't arrive as fact triples — they look like
examples/data/org_chart.json: an HR-style directory with employees
(name / title / company / office) and partner companies. When you already
KNOW the relationships, mold each record into FactTriples instead of letting
extraction re-derive them: one employee row becomes WORKS_AT (+ RESPONSIBLE
and LOCATED_AT when those fields are present), one partner row becomes
SUPPLIES + LOCATED_AT. Every fact_name is a declared edge type from
example_ontology.py, and every triple is validated at construction (fact
<= 250 chars, SCREAMING_SNAKE_CASE fact_name, node names <= 50 chars) before
the first API call.

This example also spells out the manual lifecycle the one-liners do for you:
create the graph, set the ontology, then ingest. For narrative data where
Zep extracts the relationships instead, see json_records_example.py.

Usage:
    export ZEP_API_KEY=...
    python fact_triples_example.py
"""

import json
import time
from pathlib import Path

from example_ontology import ONTOLOGY
from zep_cloud.client import Zep

from zep_ingest import FactTriple, ingest_fact_triples, search_when_ready

DATA = Path(__file__).parent / "data"

# Entity types the directory export doesn't carry — a small lookup fills the
# gap. Node labels tie triple-created nodes to the declared ontology types;
# without them the nodes stay untyped. Indexed strictly ([] not .get) so a new
# portfolio name in the data fails loudly here instead of mislabeling a node.
PORTFOLIO_TYPES = {"Atlas": "Project", "FleetView": "Product"}


def employee_triples(row: dict) -> list[FactTriple]:
    """One directory row -> explicit facts. Adapt the field names to yours."""
    person, company = row["name"], row["company"]
    since = f"{row['since']}T00:00:00Z" if row.get("since") else None
    triples = [
        FactTriple(
            fact=f"{person} is {row['title']} at {company}",
            fact_name="WORKS_AT",
            source_node_name=person,
            source_node_labels=["Person"],
            source_node_summary=f"{row['title']} at {company}",
            target_node_name=company,
            target_node_labels=["Organization"],
            valid_at=since,
        )
    ]
    if row.get("responsible_for"):
        owned = row["responsible_for"]
        triples.append(
            FactTriple(
                fact=f"{person} is responsible for {owned}",
                fact_name="RESPONSIBLE",
                source_node_name=person,
                source_node_labels=["Person"],
                target_node_name=owned,
                target_node_labels=[PORTFOLIO_TYPES[owned]],
                valid_at=since,
            )
        )
    if row.get("office"):
        triples.append(
            FactTriple(
                fact=f"{person} is based in {row['office']}",
                fact_name="LOCATED_AT",
                source_node_name=person,
                source_node_labels=["Person"],
                target_node_name=row["office"],
                target_node_labels=["Location"],
                valid_at=since,
            )
        )
    return triples


def partner_triples(row: dict) -> list[FactTriple]:
    company = row["company"]
    since = f"{row['since']}T00:00:00Z" if row.get("since") else None
    triples = [
        FactTriple(
            fact=f"{company} supplies {target}",
            fact_name="SUPPLIES",
            source_node_name=company,
            source_node_labels=["Organization"],
            source_node_summary=f"{row.get('relationship', 'partner')} of Meridian Robotics",
            target_node_name=target,
            # supplies_to names a company unless it's a known portfolio item
            target_node_labels=[PORTFOLIO_TYPES.get(target, "Organization")],
            valid_at=since,
        )
        for target in row.get("supplies_to", [])
    ]
    if row.get("relationship") == "customer":
        triples.append(
            FactTriple(
                fact=f"{company} is a customer of Meridian Robotics",
                fact_name="CUSTOMER_OF",
                source_node_name=company,
                source_node_labels=["Organization"],
                target_node_name="Meridian Robotics",
                target_node_labels=["Organization"],
                valid_at=since,
            )
        )
    if row.get("hq"):
        triples.append(
            FactTriple(
                fact=f"{company} is headquartered in {row['hq']}",
                fact_name="LOCATED_AT",
                source_node_name=company,
                source_node_labels=["Organization"],
                target_node_name=row["hq"],
                target_node_labels=["Location"],
                valid_at=since,
            )
        )
    return triples


def load_triples() -> list[FactTriple]:
    directory = json.loads((DATA / "org_chart.json").read_text())
    triples: list[FactTriple] = []
    for row in directory["employees"]:
        triples.extend(employee_triples(row))
    for row in directory["partners"]:
        triples.extend(partner_triples(row))
    return triples


def main() -> None:
    client = Zep()  # reads ZEP_API_KEY
    graph_id = f"example-triples-{int(time.time())}"

    # 1. Create the graph.
    client.graph.create(graph_id=graph_id)

    # 2. Set the ontology BEFORE any data flows — it is not retroactive, and
    #    the fact_name values below are its declared edge types.
    client.graph.set_ontology(
        entities=ONTOLOGY["entities"],
        edges=ONTOLOGY["edges"],
        graph_ids=[graph_id],
    )

    # 3. Mold the directory export into triples, then ingest. All validation
    #    already happened at FactTriple construction — before any API call.
    triples = load_triples()
    result = ingest_fact_triples(client, triples, graph_id=graph_id)
    result.raise_for_status()
    print(f"Molded org_chart.json into {len(triples)} fact triples: {result.status}")

    # search indexing lags ingestion slightly; search_when_ready absorbs that
    query = "Who works at Meridian Robotics?"
    response = search_when_ready(client, query, graph_id=graph_id, limit=5)
    print(f"\nSearch: {query}")
    for edge in response.edges or []:
        print(f"  - {edge.fact}")

    print(f"\nGraph: {graph_id}")
    print(f"Explore it at https://app.getzep.com (Graph -> {graph_id})")


if __name__ == "__main__":
    main()
