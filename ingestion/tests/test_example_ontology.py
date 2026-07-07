"""The examples ship a starter ontology — verify it converts to a valid
set_ontology payload and follows the documented design practices."""

import sys
from pathlib import Path

from zep_cloud.external_clients.ontology import (
    edge_model_to_api_schema,
    entity_model_to_api_schema,
)

sys.path.insert(0, str(Path(__file__).parent.parent / "examples"))

from example_ontology import ONTOLOGY  # noqa: E402


def _api_payload():
    entities = [
        entity_model_to_api_schema(model, name) for name, model in ONTOLOGY["entities"].items()
    ]
    edges = []
    for name, (model, source_targets) in ONTOLOGY["edges"].items():
        edge = edge_model_to_api_schema(model, name)
        edge["source_targets"] = [st.dict() for st in source_targets]
        edges.append(edge)
    return entities, edges


def test_within_api_limits():
    entities, edges = _api_payload()
    assert 0 < len(entities) <= 10
    assert 0 < len(edges) <= 10
    for entity in entities:
        assert len(entity.get("properties", [])) <= 10
    for item in entities + edges:
        assert len(item.get("description", "")) <= 500, (
            f"{item['name']} description is {len(item['description'])} chars; the API caps at 500"
        )


def test_every_edge_endpoint_is_a_declared_entity():
    entities, edges = _api_payload()
    declared = {e["name"] for e in entities}
    for edge in edges:
        assert edge["source_targets"], f"{edge['name']} has no source→target signatures"
        for st in edge["source_targets"]:
            assert st["source"] in declared, f"{edge['name']}: {st['source']} not declared"
            assert st["target"] in declared, f"{edge['name']}: {st['target']} not declared"


def test_every_type_has_a_description():
    entities, edges = _api_payload()
    for item in entities + edges:
        assert item.get("description"), f"{item['name']} is missing a description"


def test_descriptions_follow_documented_style():
    """Zep's docs + shipped default ontology: entity descriptions carry an
    'Examples:' anchor; edge descriptions are framed as extractable facts
    ('Represents the fact that ...')."""
    entities, edges = _api_payload()
    for entity in entities:
        assert "Examples:" in entity["description"], (
            f"{entity['name']} description needs an 'Examples:' list"
        )
    for edge in edges:
        assert edge["description"].startswith("Represents the fact that"), (
            f"{edge['name']} description should start 'Represents the fact that'"
        )


def test_field_descriptions_have_example_anchors():
    entities, _ = _api_payload()
    for entity in entities:
        for prop in entity.get("properties", []):
            assert "for example" in prop["description"].lower(), (
                f"{entity['name']}.{prop['name']} description needs a 'for example:' anchor"
            )


def test_responsible_enumerates_synonyms_and_forbids_derived_variants():
    _, edges = _api_payload()
    responsible = next(e for e in edges if e["name"] == "RESPONSIBLE")
    description = responsible["description"].lower()
    for synonym in ("owns", "leads", "manages"):
        assert synonym in description
    assert "never invent" in description


def test_works_at_is_contrastive_with_responsible():
    _, edges = _api_payload()
    works_at = next(e for e in edges if e["name"] == "WORKS_AT")
    assert "RESPONSIBLE" in works_at["description"]


def test_no_reserved_attribute_names():
    # per docs: these attribute names are reserved and rejected by the API
    reserved = {"uuid", "name", "graph_id", "name_embedding", "summary", "created_at"}
    entities, edges = _api_payload()
    for item in entities + edges:
        for prop in item.get("properties", []):
            assert prop["name"] not in reserved, f"{item['name']}.{prop['name']} is reserved"


def test_default_type_name_reuse_is_deliberate():
    """Zep's default ontology applies to USER graphs only — named/business
    graphs (what the examples target) have no default types, so everything the
    data needs must be declared. Where we reuse a default's name (Organization,
    Location, LOCATED_AT) that is deliberate: same semantics, consistent naming
    across graph kinds. Singleton chat-graph types must never be declared."""
    entities, edges = _api_payload()
    entity_names = {e["name"] for e in entities}
    edge_names = {e["name"] for e in edges}
    allowed_reuse = {"Organization", "Location", "Event", "Object", "Topic", "Document"}
    user_graph_singletons = {"User", "Assistant", "Preference"}
    assert not (entity_names & user_graph_singletons)
    defaults = allowed_reuse | user_graph_singletons
    assert (entity_names & defaults) <= allowed_reuse
    assert "OCCURRED_AT" not in edge_names or "Event" in entity_names
