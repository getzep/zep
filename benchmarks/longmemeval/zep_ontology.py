#!/usr/bin/env python3
"""
Zep Knowledge Graph Ontology for Domain-Agnostic Memory Systems

This module defines custom entity and edge types for Zep knowledge graphs, designed to
complement the default types (User, Preference, Procedure) while avoiding domain overfitting.

Usage:
    from zep_ontology import setup_zep_ontology
    setup_zep_ontology(client)
"""

from zep_cloud.external_clients.ontology import (
    EntityModel,
    EntityText,
    EdgeModel,
    EntityBoolean,
)
from zep_cloud import EntityEdgeSourceTarget
from pydantic import Field
from typing import Dict, List, Tuple, Any


# =============================================================================
# CUSTOM ENTITY TYPES
# =============================================================================


class Location(EntityModel):
    """
    Represents a physical or virtual place where activities occur or entities exist.
    Examples: home, office, New York, restaurant, website, virtual meeting room.
    """

    location_type: EntityText = Field(
        description="The type of location: physical, virtual, geographic, building, room, etc.",
        default=None,
    )
    address: EntityText = Field(
        description="The address, URL, or specific identifier for this location",
        default=None,
    )


class Event(EntityModel):
    """
    Represents a time-bound activity, occurrence, or experience.
    Examples: meeting, vacation, appointment, project, celebration, accident.
    """

    event_type: EntityText = Field(
        description="The category of event: meeting, appointment, trip, project, social, emergency, etc.",
        default=None,
    )
    duration: EntityText = Field(
        description="How long the event lasted or is expected to last", default=None
    )
    status: EntityText = Field(
        description="The current status: planned, ongoing, completed, cancelled",
        default=None,
    )


class Object(EntityModel):
    """
    Represents a physical item, tool, device, or possession.
    Examples: car, phone, book, medication, equipment, furniture.
    """

    object_type: EntityText = Field(
        description="The category of object: device, vehicle, tool, furniture, clothing, document, etc.",
        default=None,
    )
    brand: EntityText = Field(
        description="The brand, manufacturer, or creator of the object", default=None
    )
    condition: EntityText = Field(
        description="The current condition or status: new, used, broken, working, etc.",
        default=None,
    )


class Topic(EntityModel):
    """
    Represents a subject of conversation, interest, or knowledge domain.
    Examples: health, technology, sports, politics, work projects, hobbies.
    """

    domain: EntityText = Field(
        description="The broader domain this topic belongs to: health, technology, entertainment, business, etc.",
        default=None,
    )
    expertise_level: EntityText = Field(
        description="The user's level of knowledge or interest: beginner, intermediate, expert, professional",
        default=None,
    )


class Organization(EntityModel):
    """
    Represents a company, institution, group, or formal entity.
    Examples: employer, school, hospital, government agency, club, team.
    """

    organization_type: EntityText = Field(
        description="The type of organization: company, school, hospital, government, nonprofit, etc.",
        default=None,
    )
    industry: EntityText = Field(
        description="The industry or sector this organization operates in", default=None
    )
    relationship: EntityText = Field(
        description="The user's relationship to this organization: employee, customer, member, patient, etc.",
        default=None,
    )


class Document(EntityModel):
    """
    Represents information content in various forms.
    Examples: book, article, report, email, video, podcast, presentation.
    """

    document_type: EntityText = Field(
        description="The type of document: book, article, video, podcast, email, report, etc.",
        default=None,
    )
    author: EntityText = Field(
        description="The creator, author, or source of the document", default=None
    )
    subject: EntityText = Field(
        description="The main subject or topic the document covers", default=None
    )


# =============================================================================
# CUSTOM EDGE TYPES
# =============================================================================


class LocatedAt(EdgeModel):
    """
    Represents that an entity exists or occurs at a specific location.
    """

    context: EntityText = Field(
        description="Additional context about the location relationship: lives at, works at, visited, etc.",
        default=None,
    )


class OccurredAt(EdgeModel):
    """
    Represents that an event happened at a specific time or location.
    """

    frequency: EntityText = Field(
        description="How often this occurs: once, daily, weekly, occasionally, etc.",
        default=None,
    )


class ParticipatedIn(EdgeModel):
    """
    Represents that a user took part in an event or activity.
    """

    role: EntityText = Field(
        description="The user's role in this event: organizer, participant, attendee, leader, etc.",
        default=None,
    )
    outcome: EntityText = Field(
        description="The result or outcome of participation: successful, enjoyed, learned, etc.",
        default=None,
    )


class Owns(EdgeModel):
    """
    Represents ownership or possession of an object.
    """

    acquisition_date: EntityText = Field(
        description="When the user acquired this object", default=None
    )
    usage_frequency: EntityText = Field(
        description="How often the user uses this object: daily, weekly, rarely, etc.",
        default=None,
    )


class Uses(EdgeModel):
    """
    Represents usage or interaction with an object without ownership.
    """

    purpose: EntityText = Field(
        description="Why or how the user uses this object", default=None
    )
    access_method: EntityText = Field(
        description="How the user accesses this object: borrowed, rented, shared, workplace, etc.",
        default=None,
    )


class WorksFor(EdgeModel):
    """
    Represents employment or professional relationship with an organization.
    """

    position: EntityText = Field(
        description="The user's job title or role in the organization", default=None
    )
    employment_type: EntityText = Field(
        description="Type of relationship: full-time, part-time, contractor, volunteer, etc.",
        default=None,
    )


class Discusses(EdgeModel):
    """
    Represents that a user talks about or is interested in a topic.
    """

    interest_level: EntityText = Field(
        description="Level of interest or engagement: casual, passionate, professional, etc.",
        default=None,
    )
    context: EntityText = Field(
        description="In what context this topic is discussed: work, hobby, concern, goal, etc.",
        default=None,
    )


class RelatesTo(EdgeModel):
    """
    Represents a general conceptual or contextual relationship between entities.
    """

    relationship_type: EntityText = Field(
        description="The nature of the relationship: caused by, similar to, part of, depends on, etc.",
        default=None,
    )
    strength: EntityText = Field(
        description="How strong or important this relationship is: weak, moderate, strong, critical",
        default=None,
    )


# =============================================================================
# ONTOLOGY SETUP FUNCTION
# =============================================================================


def get_entity_definitions() -> Dict[str, Any]:
    """
    Returns the custom entity type definitions for Zep ontology setup.

    Returns:
        Dict mapping entity names to their class definitions
    """
    return {
        "Location": Location,
        "Event": Event,
        "Object": Object,
        "Topic": Topic,
        "Organization": Organization,
        "Document": Document,
    }


def get_edge_definitions() -> Dict[str, Tuple[Any, List[EntityEdgeSourceTarget]]]:
    """
    Returns the custom edge type definitions with source/target constraints for Zep ontology setup.

    Returns:
        Dict mapping edge names to tuples of (EdgeModel class, source/target constraints)
    """
    return {
        "LOCATED_AT": (
            LocatedAt,
            [
                EntityEdgeSourceTarget(source="User", target="Location"),
                EntityEdgeSourceTarget(source="Event", target="Location"),
                EntityEdgeSourceTarget(source="Object", target="Location"),
                EntityEdgeSourceTarget(source="Organization", target="Location"),
            ],
        ),
        "OCCURRED_AT": (
            OccurredAt,
            [
                EntityEdgeSourceTarget(source="Event", target="Location"),
                EntityEdgeSourceTarget(source="Event"),  # Can link to any time entity
            ],
        ),
        "PARTICIPATED_IN": (
            ParticipatedIn,
            [
                EntityEdgeSourceTarget(source="User", target="Event"),
            ],
        ),
        "OWNS": (
            Owns,
            [
                EntityEdgeSourceTarget(source="User", target="Object"),
            ],
        ),
        "USES": (
            Uses,
            [
                EntityEdgeSourceTarget(source="User", target="Object"),
                EntityEdgeSourceTarget(source="User", target="Document"),
            ],
        ),
        "WORKS_FOR": (
            WorksFor,
            [
                EntityEdgeSourceTarget(source="User", target="Organization"),
            ],
        ),
        "DISCUSSES": (
            Discusses,
            [
                EntityEdgeSourceTarget(source="User", target="Topic"),
            ],
        ),
        "RELATES_TO": (
            RelatesTo,
            [
                # Allow flexible relationships between any entities
                EntityEdgeSourceTarget(),  # No constraints - most flexible
            ],
        ),
    }


async def setup_zep_ontology(zep_client) -> None:
    """
    Set up the complete Zep ontology with custom entity and edge types.

    This function configures Zep with domain-agnostic entity and edge types designed
    to complement the default types (User, Preference, Procedure) while avoiding
    overfitting to specific domains.

    Args:
        zep_client: Initialized Zep client instance

    Example:
        from zep_cloud import ZepClient
        from zep_ontology import setup_zep_ontology

        client = ZepClient(api_key="your-api-key")
        setup_zep_ontology(client)
    """
    entities = get_entity_definitions()
    edges = get_edge_definitions()

    await zep_client.graph.set_ontology(entities=entities, edges=edges)
