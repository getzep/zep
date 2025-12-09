"""Custom ontology definitions for LOCOMO evaluation harness."""

from zep_cloud.external_clients.ontology import EntityModel
from pydantic import Field


class Person(EntityModel):
    """A person entity representing individuals in conversations."""


class Preference(EntityModel):
    """
    IMPORTANT: Prioritize this classification over ALL other classifications except User and Assistant.

    Represents entities mentioned in contexts expressing user preferences, choices, opinions, or selections. Use LOW THRESHOLD for sensitivity.

    Trigger patterns: "I want/like/prefer/choose X", "I don't want/dislike/avoid/reject Y", "X is better/worse", "rather have X than Y", "no X please", "skip X", "go with X instead", etc. Here, X or Y should be classified as Preference.
    """

    ...


class Location(EntityModel):
    """
    IMPORTANT: Before using this classification, first check if the entity is a:
    User, Assistant, Preference, Organization, Document, Event - if so, use those instead.

    Represents a physical or virtual place where activities occur or entities exist.
    Examples: home, office, New York, restaurant, website, virtual meeting room.
    """

    ...


class Event(EntityModel):
    """
    Represents a time-bound activity, occurrence, or experience.
    Examples: meeting, vacation, appointment, project, celebration, accident.
    """

    ...


class Object(EntityModel):
    """
    IMPORTANT: Use this classification ONLY as a last resort. First check if entity fits into:
    User, Assistant, Preference, Organization, Document, Event, Location, Topic - if so, use those instead.

    Represents a physical item, tool, device, or possession.
    Examples: car, phone, book, medication, equipment, furniture.
    """

    ...


class Topic(EntityModel):
    """
    IMPORTANT: Use this classification ONLY as a last resort. First check if entity fits into:
    User, Assistant, Preference, Organization, Document, Event, Location - if so, use those instead.

    Represents a subject of conversation, interest, or knowledge domain.
    Examples: health, technology, sports, politics, work projects, hobbies.
    """

    ...


class Organization(EntityModel):
    """
    Represents a company, institution, group, or formal entity.
    Examples: employer, school, hospital, government agency, club, team.
    """

    ...


class Document(EntityModel):
    """
    Represents information content in various forms.
    Examples: book, article, report, email, video, podcast, presentation.
    """

    ...


ZEP_NODE_ONTOLOGY_V2 = {
    "Person": Person,
    "Preference": Preference,
    "Location": Location,
    "Event": Event,
    "Object": Object,
    "Topic": Topic,
    "Organization": Organization,
    "Document": Document,
}
