"""
Zep Custom Ontology — Document Graphs

Defines the ontology for standalone document graphs (reference material).

Design principles:
- Simple, generic entity types that work across domains
- Search-optimized: entity names contain specific values for semantic search
- 1-2 attributes per entity following Zep best practices
- Rich descriptions for full-text search on facts

Entity types:
- Concept: Key ideas, terms, or definitions from reference material
- Topic: Subject areas or categories that organize information
- Process: Procedures, workflows, or methodologies
- Specification: Rules, requirements, or constraints
- Component: System parts, tools, features, or products

Edge types model relationships and enable sophisticated queries.
"""

from pydantic import Field
from zep_cloud.external_clients.ontology import EntityModel, EdgeModel, EntityText


# ============================================================================
# Entity Types (5 entities)
# ============================================================================

EMPTY_STRING = "Empty string if not available or applicable."
MAX_LENGTH = 50


class Concept(EntityModel):
    """A key idea, term, or definition from reference material."""

    domain: EntityText = Field(
        default=None,
        description="technical, business, legal, medical, scientific, general, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Topic(EntityModel):
    """A subject area or category that organizes information."""

    scope: EntityText = Field(
        default=None,
        description="broad, narrow, cross-cutting, foundational, advanced, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Process(EntityModel):
    """A procedure, workflow, or methodology described in documentation."""

    process_type: EntityText = Field(
        default=None,
        description="workflow, procedure, methodology, pipeline, protocol, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Specification(EntityModel):
    """A rule, requirement, or constraint defined in documentation."""

    spec_type: EntityText = Field(
        default=None,
        description="requirement, constraint, policy, guideline, standard, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Component(EntityModel):
    """A system part, tool, feature, or product referenced in documentation."""

    component_type: EntityText = Field(
        default=None,
        description="service, module, library, tool, feature, platform, api, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


# ============================================================================
# Edge Types (5 relationships)
# ============================================================================


class Describes(EdgeModel):
    """Connects a Topic to a Concept, Component, or Process it describes."""

    description_scope: EntityText = Field(
        default=None,
        description="defines, explains, summarizes, introduces, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class DependsOn(EdgeModel):
    """A Component or Process depends on another Component, Concept, or Spec."""

    dependency_type: EntityText = Field(
        default=None,
        description="requires, extends, builds_on, prerequisite, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class PartOf(EdgeModel):
    """A Component, Concept, or Process is part of a larger Component or Topic."""

    hierarchy_level: EntityText = Field(
        default=None,
        description="subsystem, subtopic, phase, subcomponent, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class References(EdgeModel):
    """A Concept, Topic, or Specification cross-references another."""

    reference_type: EntityText = Field(
        default=None,
        description="cites, relates_to, supersedes, complements, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Implements(EdgeModel):
    """A Component or Process implements a Specification or Concept."""

    conformance: EntityText = Field(
        default=None,
        description="full, partial, alternative, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


# ============================================================================
# Constants
# ============================================================================

DOCUMENT_ENTITY_TYPES = ["Concept", "Topic", "Process", "Specification", "Component"]

DOCUMENT_EDGE_TYPES = [
    "DESCRIBES",
    "DEPENDS_ON",
    "PART_OF",
    "REFERENCES",
    "IMPLEMENTS",
]


# ============================================================================
# Setup Function
# ============================================================================


async def set_document_custom_ontology(zep_client, graph_ids=None):
    """
    Set a custom ontology for standalone document graphs.

    This ontology is designed for reference documents and captures:
    - Concepts, terms, and definitions
    - Topics and subject areas
    - Processes and workflows
    - Specifications and requirements
    - Components, tools, and features

    Args:
        zep_client: AsyncZep client instance
        graph_ids: Optional list of graph IDs to apply ontology to.
                  If None, applies to entire project.

    Returns:
        Response from set_ontology call
    """
    from zep_cloud import EntityEdgeSourceTarget

    kwargs = {
        "entities": {
            "Concept": Concept,
            "Topic": Topic,
            "Process": Process,
            "Specification": Specification,
            "Component": Component,
        },
        "edges": {
            "DESCRIBES": (
                Describes,
                [
                    EntityEdgeSourceTarget(source="Topic", target="Concept"),
                    EntityEdgeSourceTarget(source="Topic", target="Component"),
                    EntityEdgeSourceTarget(source="Topic", target="Process"),
                ],
            ),
            "DEPENDS_ON": (
                DependsOn,
                [
                    EntityEdgeSourceTarget(source="Component", target="Component"),
                    EntityEdgeSourceTarget(source="Component", target="Concept"),
                    EntityEdgeSourceTarget(source="Process", target="Component"),
                    EntityEdgeSourceTarget(source="Process", target="Specification"),
                ],
            ),
            "PART_OF": (
                PartOf,
                [
                    EntityEdgeSourceTarget(source="Component", target="Component"),
                    EntityEdgeSourceTarget(source="Concept", target="Topic"),
                    EntityEdgeSourceTarget(source="Process", target="Topic"),
                ],
            ),
            "REFERENCES": (
                References,
                [
                    EntityEdgeSourceTarget(source="Concept", target="Concept"),
                    EntityEdgeSourceTarget(source="Topic", target="Topic"),
                    EntityEdgeSourceTarget(source="Specification", target="Specification"),
                    EntityEdgeSourceTarget(source="Specification", target="Concept"),
                ],
            ),
            "IMPLEMENTS": (
                Implements,
                [
                    EntityEdgeSourceTarget(source="Component", target="Specification"),
                    EntityEdgeSourceTarget(source="Process", target="Specification"),
                    EntityEdgeSourceTarget(source="Component", target="Concept"),
                ],
            ),
        },
    }

    if graph_ids:
        kwargs["graph_ids"] = graph_ids

    response = await zep_client.graph.set_ontology(**kwargs)
    return response
