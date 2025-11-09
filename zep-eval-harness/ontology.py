"""
Zep Custom Ontology

This ontology defines entity and edge types optimized for general conversational assistants.

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
# Ontology Constants - Single Source of Truth
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
# Ontology Setup Function
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
