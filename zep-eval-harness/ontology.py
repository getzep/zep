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
    """A person mentioned in conversation (family, friends, colleagues, etc.).
    Entity names should be the person's name.
    Descriptions should contain relationship to user, age, occupation, or other relevant details.
    """

    relationship: EntityText = Field(
        default=None,
        description="family, friend, colleague, professional, acquaintance, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Location(EntityModel):
    """A physical place or address.
    Entity names should be the location name or address.
    Descriptions should contain address details, purpose, or context about the location.
    """

    location_type: EntityText = Field(
        default=None,
        description="home, office, clinic, store, restaurant, park, school, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Organization(EntityModel):
    """A company, institution, or group.
    Entity names should be the organization name.
    Descriptions should contain type of organization, services provided, or user's relationship to it.
    """

    org_type: EntityText = Field(
        default=None,
        description="company, school, hospital, store, service_provider, government, nonprofit, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Event(EntityModel):
    """An appointment, meeting, or scheduled activity.
    Entity names should describe the event and include date/time if specific.
    Descriptions should contain location, participants, purpose, and any special details.
    """

    event_type: EntityText = Field(
        default=None,
        description="appointment, meeting, class, activity, celebration, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Item(EntityModel):
    """A physical object, pet, or possession mentioned in conversation.
    Entity names should be the item name or description.
    Descriptions should contain type, purpose, condition, or other relevant details.
    """

    item_type: EntityText = Field(
        default=None,
        description="pet, vehicle, device, tool, furniture, clothing, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


# ============================================================================
# Edge Types (6 relationships, no attributes)
# ============================================================================


class RelatedTo(EdgeModel):
    """Connects a Person to another Person or to the User.
    Description should explain the nature of the relationship."""

    ...


class LocatedAt(EdgeModel):
    """Connects an Event, Person, or Item to a Location.
    Description can provide additional context about the location relationship."""

    ...


class WorksFor(EdgeModel):
    """Connects a Person to an Organization where they work or are affiliated.
    Description can include role, duration, or other employment details."""

    ...


class Owns(EdgeModel):
    """User or Person owns an Item.
    Description can include acquisition date, condition, or purpose."""

    ...


class ScheduledAt(EdgeModel):
    """Connects an Event to a specific date/time or Location.
    Description should include timing details and any special arrangements."""

    ...


class Involves(EdgeModel):
    """Connects an Event to a Person, Item, or Organization that participates or is involved.
    Description should explain the nature of involvement."""

    ...


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
            # Entity located at a Location
            "LOCATED_AT": (
                LocatedAt,
                [
                    EntityEdgeSourceTarget(source="Event", target="Location"),
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
    """A key idea, term, or definition from reference material.
    Entity names should be the concept name or term.
    Descriptions should contain the definition, explanation, or significance.
    """

    domain: EntityText = Field(
        default=None,
        description="technical, business, legal, medical, scientific, general, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Topic(EntityModel):
    """A subject area or category that organizes information.
    Entity names should be the topic or subject name.
    Descriptions should contain scope, relevance, or summary of the topic.
    """

    scope: EntityText = Field(
        default=None,
        description="broad, narrow, cross-cutting, foundational, advanced, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Process(EntityModel):
    """A procedure, workflow, or methodology described in documentation.
    Entity names should describe the process (e.g. 'User Onboarding Flow').
    Descriptions should contain purpose, steps overview, or when it applies.
    """

    process_type: EntityText = Field(
        default=None,
        description="workflow, procedure, methodology, pipeline, protocol, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Specification(EntityModel):
    """A rule, requirement, or constraint defined in documentation.
    Entity names should capture the rule or requirement concisely.
    Descriptions should contain the full specification and any conditions.
    """

    spec_type: EntityText = Field(
        default=None,
        description="requirement, constraint, policy, guideline, standard, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Component(EntityModel):
    """A system part, tool, feature, or product referenced in documentation.
    Entity names should be the component or product name.
    Descriptions should contain purpose, capabilities, or how it fits in the system.
    """

    component_type: EntityText = Field(
        default=None,
        description="service, module, library, tool, feature, platform, api, other. "
        + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


# ============================================================================
# Document Edge Types (5 relationships, no attributes)
# ============================================================================


class Describes(EdgeModel):
    """Connects a Topic to a Concept, Component, or Process it describes.
    Description should explain the nature of the description relationship."""

    ...


class DependsOn(EdgeModel):
    """One Component or Process depends on another Component, Concept, or Specification.
    Description should explain the nature of the dependency."""

    ...


class PartOf(EdgeModel):
    """A Component, Concept, or Process is part of a larger Component or Topic.
    Description should explain the hierarchical relationship."""

    ...


class References(EdgeModel):
    """One Concept, Topic, or Specification cross-references another.
    Description should explain the nature of the reference."""

    ...


class Implements(EdgeModel):
    """A Component or Process implements a Specification or Concept.
    Description should explain how the implementation relates to the spec."""

    ...


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
