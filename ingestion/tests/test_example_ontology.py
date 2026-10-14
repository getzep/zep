"""The examples ship a starter ontology — verify it is a valid v4 set_ontology
payload and follows the documented design practices."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "examples"))

from example_ontology import EDGE_TYPES, ENTITY_TYPES  # noqa: E402


def test_within_api_limits():
    assert 0 < len(ENTITY_TYPES) <= 10
    assert 0 < len(EDGE_TYPES) <= 10
    for entity in ENTITY_TYPES:
        assert len(entity.properties or []) <= 10
    for item in [*ENTITY_TYPES, *EDGE_TYPES]:
        assert len(item.description or "") <= 500, (
            f"{item.name} description is {len(item.description or '')} chars; the API caps at 500"
        )


def test_every_edge_endpoint_is_a_declared_entity():
    declared = {entity.name for entity in ENTITY_TYPES}
    for edge in EDGE_TYPES:
        assert edge.source_targets, f"{edge.name} has no source→target signatures"
        for signature in edge.source_targets:
            assert signature.source in declared, f"{edge.name}: {signature.source} not declared"
            assert signature.target in declared, f"{edge.name}: {signature.target} not declared"


def test_every_type_has_a_description():
    for item in [*ENTITY_TYPES, *EDGE_TYPES]:
        assert item.description, f"{item.name} is missing a description"


def test_descriptions_follow_documented_style():
    """Zep's docs + shipped default ontology: entity descriptions carry an
    'Examples:' anchor; edge descriptions are framed as extractable facts
    ('Represents the fact that ...')."""
    for entity in ENTITY_TYPES:
        assert "Examples:" in (entity.description or ""), (
            f"{entity.name} description needs an 'Examples:' list"
        )
    for edge in EDGE_TYPES:
        assert (edge.description or "").startswith("Represents the fact that"), (
            f"{edge.name} description should start 'Represents the fact that'"
        )


def test_property_descriptions_have_example_anchors():
    for entity in ENTITY_TYPES:
        for prop in entity.properties or []:
            assert "for example" in (prop.description or "").lower(), (
                f"{entity.name}.{prop.name} description needs a 'for example:' anchor"
            )


def test_responsible_enumerates_synonyms_and_forbids_derived_variants():
    responsible = next(edge for edge in EDGE_TYPES if edge.name == "RESPONSIBLE")
    description = (responsible.description or "").lower()
    for synonym in ("owns", "leads", "manages"):
        assert synonym in description
    assert "never invent" in description


def test_works_at_is_contrastive_with_responsible():
    works_at = next(edge for edge in EDGE_TYPES if edge.name == "WORKS_AT")
    assert "RESPONSIBLE" in (works_at.description or "")


def test_no_reserved_property_names():
    # per docs: these property names are reserved and rejected by the API
    reserved = {"uuid", "name", "graph_id", "name_embedding", "summary", "created_at"}
    for item in [*ENTITY_TYPES, *EDGE_TYPES]:
        for prop in item.properties or []:
            assert prop.name not in reserved, f"{item.name}.{prop.name} is reserved"


def test_default_type_name_reuse_is_deliberate():
    """Zep's default ontology applies to USER graphs only — named/business
    graphs (what the examples target) have no default types, so everything the
    data needs must be declared. Where we reuse a default's name (Organization,
    Location, LOCATED_AT) that is deliberate: same semantics, consistent naming
    across graph kinds. Singleton chat-graph types must never be declared."""
    entity_names = {entity.name for entity in ENTITY_TYPES}
    edge_names = {edge.name for edge in EDGE_TYPES}
    allowed_reuse = {"Organization", "Location", "Event", "Object", "Topic", "Document"}
    user_graph_singletons = {"User", "Assistant", "Preference"}
    assert not (entity_names & user_graph_singletons)
    defaults = allowed_reuse | user_graph_singletons
    assert (entity_names & defaults) <= allowed_reuse
    assert "OCCURRED_AT" not in edge_names or "Event" in entity_names
