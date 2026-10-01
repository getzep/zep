"""
Zep Custom Ontology — User Graphs

Defines the ontology for user conversation graphs (personal assistants).

Design principles:
- Simple, generic entity types that work across domains
- Search-optimized: entity names contain specific values for semantic search
- 1-2 attributes per entity following Zep best practices
- Rich descriptions for full-text search on facts

Entity types:
- Person: People mentioned in conversations (family, friends, colleagues, etc.)
- Location: Physical places or addresses
- Organization: Companies, institutions, or groups
- Event: Appointments, meetings, or scheduled activities
- Item: Physical objects, pets, or possessions

Edge types model relationships and enable sophisticated queries.
"""

from pydantic import Field
from zep_cloud.ontology import EntityModel, EdgeModel, EntityText, build_ontology
from zep_cloud.types import EdgeSourceTarget


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
        description="lives_at, works_at, located_in, visits, other. " + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class WorksFor(EdgeModel):
    """Connects a Person to an Organization they work for or are affiliated with."""

    role: EntityText = Field(
        default=None,
        description="The person's role or title at the organization. " + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class Owns(EdgeModel):
    """User or Person owns an Item."""

    ownership_type: EntityText = Field(
        default=None,
        description="owns, leases, rents, borrowed, other. " + EMPTY_STRING,
        max_length=MAX_LENGTH,
    )


class ScheduledAt(EdgeModel):
    """Connects an Event to a specific date/time or Location."""

    timing: EntityText = Field(
        default=None,
        description="The date, time, or timeframe of the event. " + EMPTY_STRING,
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
# Constants - Single Source of Truth
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
# Setup Function
# ============================================================================


async def set_custom_ontology(zep_client, graph_uuids=None):
    """
    Set a custom ontology for user graphs or for the whole project.

    This ontology is designed for general conversational assistants and captures:
    - People and their relationships
    - Locations and addresses
    - Organizations and institutions
    - Events and appointments
    - Items and possessions (including pets)

    Args:
        zep_client: AsyncZep client instance
        graph_uuids: Optional list of graph UUIDs to apply the ontology to.
                 A user graph UUID is the ``graph_uuid`` of the user.
                 If None, applies to the project default.

    Returns:
        The last response from a set_ontology call
    """
    entity_types, edge_types = build_ontology(
        entities={
            "Person": Person,
            "Location": Location,
            "Organization": Organization,
            "Event": Event,
            "Item": Item,
        },
        edges={
            "RELATED_TO": (
                RelatedTo,
                [
                    EdgeSourceTarget(source="User", target="Person"),
                    EdgeSourceTarget(source="Person", target="Person"),
                ],
            ),
            "LOCATED_AT": (
                LocatedAt,
                [
                    EdgeSourceTarget(source="Person", target="Location"),
                    EdgeSourceTarget(source="Item", target="Location"),
                    EdgeSourceTarget(source="Organization", target="Location"),
                ],
            ),
            "WORKS_FOR": (
                WorksFor,
                [
                    EdgeSourceTarget(source="User", target="Organization"),
                    EdgeSourceTarget(source="Person", target="Organization"),
                ],
            ),
            "OWNS": (
                Owns,
                [
                    EdgeSourceTarget(source="User", target="Item"),
                    EdgeSourceTarget(source="Person", target="Item"),
                ],
            ),
            "SCHEDULED_AT": (
                ScheduledAt,
                [
                    EdgeSourceTarget(source="Event", target="Location"),
                ],
            ),
            "INVOLVES": (
                Involves,
                [
                    EdgeSourceTarget(source="Event", target="Person"),
                    EdgeSourceTarget(source="Event", target="Item"),
                    EdgeSourceTarget(source="Event", target="Organization"),
                ],
            ),
        },
    )

    if not graph_uuids:
        return await zep_client.project.set_ontology(
            entity_types=entity_types, edge_types=edge_types
        )

    response = None
    for graph_uuid in graph_uuids:
        response = await zep_client.graph.set_ontology(
            graph_uuid, entity_types=entity_types, edge_types=edge_types
        )
    return response
