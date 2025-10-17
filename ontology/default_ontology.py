from pydantic import BaseModel, Field


class User(BaseModel):
    """A Zep user specified by role in chat messages. There can only be a single User entity."""

    user_id: str | None = Field(..., description="user_id of the Zep user.")
    role_type: str = Field(..., description="The role assigned to the actor.")
    email: str = Field(
        ...,
        description="The user's email address, used for communication and login purposes.",
    )
    first_name: str | None = Field(..., description="The user's first name.")
    last_name: str | None = Field(..., description="The user's last name.")


class Assistant(BaseModel):
    """Represents the AI assistant in the conversation. This entity is a singleton. All entities of the AI Assistant type represent the same entity."""

    assistant_name: str | None = Field(..., description="The name of the assistant")


class Preference(BaseModel):
    """
    IMPORTANT: Prioritize this classification over ALL other classifications except User and Assistant.

    Represents entities mentioned in contexts expressing user preferences, choices, opinions, or selections. Use LOW THRESHOLD for sensitivity.

    Trigger patterns: "I want/like/prefer/choose X", "I don't want/dislike/avoid/reject Y", "X is better/worse", "rather have X than Y", "no X please", "skip X", "go with X instead", etc. Here, X or Y should be classified as Preference.
    """

    ...


class Location(BaseModel):
    """
    IMPORTANT: Before using this classification, first check if the entity is a:
    User, Assistant, Preference, Organization, Document, Event - if so, use those instead.

    Represents a physical or virtual place where activities occur or entities exist.
    Examples: home, office, New York, restaurant, website, virtual meeting room.
    """

    ...


class Event(BaseModel):
    """
    Represents a time-bound activity, occurrence, or experience.
    Examples: meeting, vacation, appointment, project, celebration, accident.
    """

    ...


class Object(BaseModel):
    """
    IMPORTANT: Use this classification ONLY as a last resort. First check if entity fits into:
    User, Assistant, Preference, Organization, Document, Event, Location, Topic - if so, use those instead.

    Represents a physical item, tool, device, or possession.
    Examples: car, phone, book, medication, equipment, furniture.
    """

    ...


class Topic(BaseModel):
    """
    IMPORTANT: Use this classification ONLY as a last resort. First check if entity fits into:
    User, Assistant, Preference, Organization, Document, Event, Location - if so, use those instead.

    Represents a subject of conversation, interest, or knowledge domain.
    Examples: health, technology, sports, politics, work projects, hobbies.
    """

    ...


class Organization(BaseModel):
    """
    Represents a company, institution, group, or formal entity.
    Examples: employer, school, hospital, government agency, club, team.
    """

    ...


class Document(BaseModel):
    """
    Represents information content in various forms.
    Examples: book, article, report, email, video, podcast, presentation.
    """

    ...


# =============================================================================
# CUSTOM EDGE TYPES
# =============================================================================


class LocatedAt(BaseModel):
    """
    Represents that an entity exists or occurs at a specific location.
    """

    ...


class OccurredAt(BaseModel):
    """
    Represents that an event happened at a specific time or location.
    """

    ...


ZEP_NODE_ONTOLOGY = {
    "User": User,
    "Assistant": Assistant,
    "Preference": Preference,
    "Location": Location,
    "Event": Event,
    "Object": Object,
    "Topic": Topic,
    "Organization": Organization,
    "Document": Document,
}

ZEP_EDGE_ONTOLOGY = {
    "LOCATED_AT": LocatedAt,
    "OCCURRED_AT": OccurredAt,
}

ZEP_EDGE_TYPE_MAP = {
    ("Event", "Entity"): ["OCCURRED_AT"],
    ("Entity", "Location"): ["LOCATED_AT"],
}
