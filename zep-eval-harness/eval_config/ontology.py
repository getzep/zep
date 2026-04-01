"""
Zep Custom Ontology

This module defines two ontologies:

1. **User ontology** — for user conversation graphs (personal assistants).
2. **Document ontology** — for standalone document graphs (reference material).

Design principles:
- Simple, generic entity types that work across domains
- Search-optimized: entity names contain specific values for semantic search
- 1-2 attributes per entity following Zep best practices
- Rich descriptions for full-text search on facts

User entity types:
- Person: People mentioned in conversations (family, friends, colleagues, etc.)
- Location: Physical places or addresses
- Organization: Companies, institutions, or groups
- Event: Appointments, meetings, or scheduled activities
- Item: Physical objects, pets, or possessions

Document entity types:
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


class Person(EntityModel):
    """A person mentioned in conversation (family, friends, colleagues, etc.)."""

    relationship: EntityText = Field(
        default=None,
        description="family, friend, colleague, professional, acquaintance, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Location(EntityModel):
    """A physical place or address."""

    location_type: EntityText = Field(
        default=None,
        description="home, office, clinic, store, restaurant, park, school, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Organization(EntityModel):
    """A company, institution, or group."""

    org_type: EntityText = Field(
        default=None,
        description="company, school, hospital, store, service_provider, government, nonprofit, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Event(EntityModel):
    """An appointment, meeting, or scheduled activity."""

    event_type: EntityText = Field(
        default=None,
        description="appointment, meeting, class, activity, celebration, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Item(EntityModel):
    """A physical object, pet, or possession mentioned in conversation."""

    item_type: EntityText = Field(
        default=None,
        description="pet, vehicle, device, tool, furniture, clothing, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


# ============================================================================
# Edge Types (6 relationships)
# ============================================================================


class RelatedTo(EdgeModel):
    """Connects a Person to another Person or to the User."""

    relationship_type: EntityText = Field(
        default=None,
        description="family, friend, colleague, neighbor, acquaintance, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class LocatedAt(EdgeModel):
    """Connects a Person, Item, or Organization to a Location."""

    context: EntityText = Field(
        default=None,
        description="lives_at, works_at, located_in, visits, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class WorksFor(EdgeModel):
    """Connects a Person to an Organization they work for or are affiliated with."""

    role: EntityText = Field(
        default=None,
        description="The person's role or title at the organization. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Owns(EdgeModel):
    """User or Person owns an Item."""

    ownership_type: EntityText = Field(
        default=None,
        description="owns, leases, rents, borrowed, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class ScheduledAt(EdgeModel):
    """Connects an Event to a specific date/time or Location."""

    timing: EntityText = Field(
        default=None,
        description="The date, time, or timeframe of the event. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Involves(EdgeModel):
    """Connects an Event to a participating Person, Item, or Organization."""

    involvement_role: EntityText = Field(
        default=None,
        description="participant, organizer, attendee, provider, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


# ============================================================================
# User Ontology Constants - Single Source of Truth
# ============================================================================

# Entity type names
ENTITY_TYPES = ["Person", "Location", "Organization", "Event", "Item"]

# Edge type names
EDGE_TYPES = [
    "RELATED_TO",
    "LOCATED_AT",
    "WORKS_FOR",
    "OWNS",
    "SCHEDULED_AT",
    "INVOLVES",
]


# ============================================================================
# User Ontology Setup Function
# ============================================================================


async def set_custom_ontology(zep_client, user_ids=None):
    """
    Set a custom ontology for a Zep project.

    This ontology is designed for general conversational assistants and captures:
    - People and their relationships
    - Locations and addresses
    - Organizations and institutions
    - Events and appointments
    - Items and possessions (including pets)

    Design philosophy:
    - Simple, generic entity types applicable across domains
    - Search-optimized entity naming (values in names)
    - Rich descriptions for full-text search
    - Flexible edge types for various relationship patterns

    Args:
        zep_client: AsyncZep client instance
        user_ids: Optional list of user IDs to apply ontology to.
                 If None, applies to entire project.

    Returns:
        Response from set_ontology call

    Example usage:
        ```python
        from zep_cloud import AsyncZep
        client = AsyncZep(api_key="your-key")
        await set_custom_ontology(client)
        ```
    """
    from zep_cloud import EntityEdgeSourceTarget

    kwargs = {
        "entities": {
            "Person": Person,
            "Location": Location,
            "Organization": Organization,
            "Event": Event,
            "Item": Item,
        },
        "edges": {
            # Person related to another Person or User
            "RELATED_TO": (
                RelatedTo,
                [
                    EntityEdgeSourceTarget(source="User", target="Person"),
                    EntityEdgeSourceTarget(source="Person", target="Person"),
                ],
            ),
            # Entity located at a Location (use SCHEDULED_AT for Events)
            "LOCATED_AT": (
                LocatedAt,
                [
                    EntityEdgeSourceTarget(source="Person", target="Location"),
                    EntityEdgeSourceTarget(source="Item", target="Location"),
                    EntityEdgeSourceTarget(source="Organization", target="Location"),
                ],
            ),
            # Person works for Organization
            "WORKS_FOR": (
                WorksFor,
                [
                    EntityEdgeSourceTarget(source="User", target="Organization"),
                    EntityEdgeSourceTarget(source="Person", target="Organization"),
                ],
            ),
            # User or Person owns Item
            "OWNS": (
                Owns,
                [
                    EntityEdgeSourceTarget(source="User", target="Item"),
                    EntityEdgeSourceTarget(source="Person", target="Item"),
                ],
            ),
            # Event scheduled at Location or time
            "SCHEDULED_AT": (
                ScheduledAt,
                [
                    EntityEdgeSourceTarget(source="Event", target="Location"),
                ],
            ),
            # Event involves Person, Item, or Organization
            "INVOLVES": (
                Involves,
                [
                    EntityEdgeSourceTarget(source="Event", target="Person"),
                    EntityEdgeSourceTarget(source="Event", target="Item"),
                    EntityEdgeSourceTarget(source="Event", target="Organization"),
                ],
            ),
        },
    }

    # Apply to specific users if provided
    if user_ids:
        kwargs["user_ids"] = user_ids

    response = await zep_client.graph.set_ontology(**kwargs)
    return response


# ============================================================================
# Document Entity Types (5 entities)
# ============================================================================


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
# Document Edge Types (5 relationships)
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
# Document Ontology Constants
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
# Document Ontology Setup Function
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
            # Topic describes Concept, Component, or Process
            "DESCRIBES": (
                Describes,
                [
                    EntityEdgeSourceTarget(source="Topic", target="Concept"),
                    EntityEdgeSourceTarget(source="Topic", target="Component"),
                    EntityEdgeSourceTarget(source="Topic", target="Process"),
                ],
            ),
            # Component/Process depends on another Component, Concept, or Specification
            "DEPENDS_ON": (
                DependsOn,
                [
                    EntityEdgeSourceTarget(source="Component", target="Component"),
                    EntityEdgeSourceTarget(source="Component", target="Concept"),
                    EntityEdgeSourceTarget(source="Process", target="Component"),
                    EntityEdgeSourceTarget(source="Process", target="Specification"),
                ],
            ),
            # Component/Concept/Process is part of a larger Component or Topic
            "PART_OF": (
                PartOf,
                [
                    EntityEdgeSourceTarget(source="Component", target="Component"),
                    EntityEdgeSourceTarget(source="Concept", target="Topic"),
                    EntityEdgeSourceTarget(source="Process", target="Topic"),
                ],
            ),
            # Cross-references between Concepts, Topics, Specifications
            "REFERENCES": (
                References,
                [
                    EntityEdgeSourceTarget(source="Concept", target="Concept"),
                    EntityEdgeSourceTarget(source="Topic", target="Topic"),
                    EntityEdgeSourceTarget(source="Specification", target="Specification"),
                    EntityEdgeSourceTarget(source="Specification", target="Concept"),
                ],
            ),
            # Component/Process implements a Specification or Concept
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
