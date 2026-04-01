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


async def set_custom_ontology(zep_client, user_ids=None):
    """
    Set a custom ontology for a Zep project.

    This ontology is designed for general conversational assistants and captures:
    - People and their relationships
    - Locations and addresses
    - Organizations and institutions
    - Events and appointments
    - Items and possessions (including pets)

    Args:
        zep_client: AsyncZep client instance
        user_ids: Optional list of user IDs to apply ontology to.
                 If None, applies to entire project.

    Returns:
        Response from set_ontology call
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
            "RELATED_TO": (
                RelatedTo,
                [
                    EntityEdgeSourceTarget(source="User", target="Person"),
                    EntityEdgeSourceTarget(source="Person", target="Person"),
                ],
            ),
            "LOCATED_AT": (
                LocatedAt,
                [
                    EntityEdgeSourceTarget(source="Person", target="Location"),
                    EntityEdgeSourceTarget(source="Item", target="Location"),
                    EntityEdgeSourceTarget(source="Organization", target="Location"),
                ],
            ),
            "WORKS_FOR": (
                WorksFor,
                [
                    EntityEdgeSourceTarget(source="User", target="Organization"),
                    EntityEdgeSourceTarget(source="Person", target="Organization"),
                ],
            ),
            "OWNS": (
                Owns,
                [
                    EntityEdgeSourceTarget(source="User", target="Item"),
                    EntityEdgeSourceTarget(source="Person", target="Item"),
                ],
            ),
            "SCHEDULED_AT": (
                ScheduledAt,
                [
                    EntityEdgeSourceTarget(source="Event", target="Location"),
                ],
            ),
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

    if user_ids:
        kwargs["user_ids"] = user_ids

    response = await zep_client.graph.set_ontology(**kwargs)
    return response
