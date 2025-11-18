"""
Real Estate Agent Ontology

This module defines custom entity and edge types for a real estate agent assistant.
The ontology captures properties, neighborhoods, schools, amenities, and the relationships
between users and these entities during the home buying process.
"""

from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel, EntityInt, EntityFloat, EntityBoolean
from pydantic import Field


# ============================================================================
# Entity Types (Nouns)
# ============================================================================

class Property(EntityModel):
    """
    Represents a specific house or real estate property that is available for sale or has been viewed.
    This includes single-family homes, condos, townhouses, etc.
    """
    address: EntityText = Field(
        description="The street address of the property, for example: '123 Maple Street' or '456 Oak Drive'",
        default=None
    )
    price: EntityInt = Field(
        description="The listing price of the property in dollars, for example: 365000 or 420000",
        default=None
    )
    bedrooms: EntityInt = Field(
        description="The number of bedrooms in the property, for example: 3, 4, or 5",
        default=None
    )
    bathrooms: EntityFloat = Field(
        description="The number of bathrooms in the property, for example: 2, 2.5, or 3",
        default=None
    )


class Neighborhood(EntityModel):
    """
    Represents a geographic area, neighborhood, or school district where properties are located.
    Examples include 'Westside', 'Riverside School District', or specific area names.
    """
    area_name: EntityText = Field(
        description="The name of the neighborhood, area, or school district, for example: 'Westside', 'Riverside School District', 'Downtown'",
        default=None
    )
    desirability: EntityText = Field(
        description="Why this neighborhood is desirable or notable, for example: 'highly rated schools', 'family-friendly', 'walkable'",
        default=None
    )


class School(EntityModel):
    """
    Represents an educational institution (elementary, middle, or high school) near a property.
    Schools are an important factor in family home buying decisions.
    """
    school_name: EntityText = Field(
        description="The name of the school, for example: 'Riverside Elementary School' or 'Lincoln Middle School'",
        default=None
    )
    school_type: EntityText = Field(
        description="The type of school, for example: 'elementary', 'middle', 'high'",
        default=None
    )
    rating: EntityText = Field(
        description="The quality or rating of the school, for example: 'highly rated', 'top-rated', or a numeric rating",
        default=None
    )


class Amenity(EntityModel):
    """
    Represents a specific feature, amenity, or characteristic of a property.
    Examples include 'hardwood floors', 'updated kitchen', 'central AC', 'backyard', 'garage', '2-car garage', 'home office'.
    """
    feature_name: EntityText = Field(
        description="The name of the feature or amenity, for example: 'hardwood floors', 'updated kitchen', 'central AC', 'backyard', '2-car garage', 'home office'",
        default=None
    )
    importance: EntityText = Field(
        description="How important this amenity is to the user, for example: 'essential', 'must-have', 'nice to have', 'preferred'",
        default=None
    )


class FamilyMember(EntityModel):
    """
    Represents a member of the user's household or family.
    This includes spouse/partner, children, parents, or other relatives that influence housing decisions.
    """
    relationship: EntityText = Field(
        description="The relationship to the user, for example: 'husband', 'wife', 'child', 'daughter', 'son', 'parent', 'mother', 'father'",
        default=None
    )
    age: EntityInt = Field(
        description="The age of the family member if mentioned, for example: 7, 10, 35",
        default=None
    )
    relevance: EntityText = Field(
        description="How this family member influences the home search, for example: 'starting middle school', 'works from home', 'visits often', 'needs play space'",
        default=None
    )


class Room(EntityModel):
    """
    Represents a specific room or space within a property that the user has requirements or preferences about.
    Examples include kitchen, master bedroom, home office, guest room, or other specific spaces.
    """
    room_type: EntityText = Field(
        description="The type of room, for example: 'kitchen', 'master bedroom', 'home office', 'guest room', 'bedroom', 'bathroom'",
        default=None
    )
    desired_features: EntityText = Field(
        description="Specific features desired for this room, for example: 'updated', 'large', 'with door', 'good-sized', 'renovated'",
        default=None
    )


class Showing(EntityModel):
    """
    Represents a scheduled property showing or tour appointment.
    This tracks when users are viewing properties.
    """
    property_address: EntityText = Field(
        description="The address of the property being shown, for example: 'Maple Street', 'Birch Lane', 'Oak Drive'",
        default=None
    )
    showing_time: EntityText = Field(
        description="When the showing is scheduled, for example: 'Saturday at 10am', 'Thursday at 5pm', 'this weekend'",
        default=None
    )
    status: EntityText = Field(
        description="The status of the showing, for example: 'scheduled', 'completed', 'cancelled'",
        default=None
    )


class FinancingDetail(EntityModel):
    """
    Represents financial and mortgage information related to the home purchase.
    This includes pre-approval amounts, mortgage details, and financing contingencies.
    """
    financing_type: EntityText = Field(
        description="The type of financing detail, for example: 'pre-approval', 'mortgage', 'financing contingency', 'down payment'",
        default=None
    )
    amount: EntityInt = Field(
        description="The dollar amount if applicable, for example: 420000 for pre-approval amount",
        default=None
    )
    details: EntityText = Field(
        description="Additional details about the financing, for example: 'pre-approved up to', 'getting a mortgage', 'financing shouldn't be an issue'",
        default=None
    )


# ============================================================================
# Edge Types (Relationships/Verbs)
# ============================================================================

class InterestedInProperty(EdgeModel):
    """
    Represents the fact that the user expressed interest in a specific property.
    This could be from reviewing listings, saving favorites, or asking to see a property.
    """
    property_address: EntityText = Field(
        description="The address of the property the user is interested in, for example: 'Maple Street' or 'Birch Lane'",
        default=None
    )
    interest_level: EntityText = Field(
        description="The level of interest, for example: 'high', 'moderate', 'considering', 'very interested'",
        default=None
    )


class ViewedProperty(EdgeModel):
    """
    Represents the fact that the user physically toured or viewed a property.
    This is a concrete action where the user visited the property for a showing.
    """
    property_address: EntityText = Field(
        description="The address of the property that was viewed, for example: 'Maple Street' or 'Oak Drive'",
        default=None
    )
    viewing_date: EntityText = Field(
        description="When the property was viewed, for example: 'Saturday', 'yesterday', 'this week', or a specific date",
        default=None
    )


class RejectedProperty(EdgeModel):
    """
    Represents the fact that the user rejected or decided against a property.
    Includes the reason for rejection, which helps refine future searches.
    """
    property_address: EntityText = Field(
        description="The address of the property that was rejected, for example: 'Maple Street' or 'Oak Drive'",
        default=None
    )
    rejection_reason: EntityText = Field(
        description="Why the property was rejected, for example: 'too cramped', 'needs too much work', 'outdated kitchen', 'not enough bathrooms'",
        default=None
    )


class MadeOffer(EdgeModel):
    """
    Represents the fact that the user made a purchase offer on a property.
    This is a significant action in the home buying process.
    """
    property_address: EntityText = Field(
        description="The address of the property where an offer was made, for example: 'Birch Lane'",
        default=None
    )
    offer_amount: EntityInt = Field(
        description="The dollar amount of the offer, for example: 375000 or 400000",
        default=None
    )


class HasRequirement(EdgeModel):
    """
    Represents a specific requirement, must-have, or criteria that the user needs in a property.
    Examples include number of bedrooms, bathrooms, features, or condition requirements.
    """
    requirement_type: EntityText = Field(
        description="The category of requirement, for example: 'bedrooms', 'bathrooms', 'condition', 'features', 'space'",
        default=None
    )
    requirement_details: EntityText = Field(
        description="Specific details about the requirement, for example: 'at least 3 bedrooms', 'move-in ready', 'home office with door', '2.5 bathrooms minimum'",
        default=None
    )
    priority: EntityText = Field(
        description="How important this requirement is, for example: 'essential', 'must-have', 'important', 'preferred', 'flexible'",
        default=None
    )


class PrefersNeighborhood(EdgeModel):
    """
    Represents the fact that the user prefers or wants to live in a specific neighborhood or area.
    This helps narrow down the property search to desired locations.
    """
    neighborhood_name: EntityText = Field(
        description="The name of the preferred neighborhood or area, for example: 'Westside', 'Riverside School District', 'near good schools'",
        default=None
    )
    reason: EntityText = Field(
        description="Why the user prefers this neighborhood, for example: 'good schools', 'family-friendly', 'convenient location', 'safe area'",
        default=None
    )


class NeedsAmenity(EdgeModel):
    """
    Represents the fact that the user needs or wants a specific amenity or feature in their property.
    This includes both essential features and nice-to-have amenities.
    """
    amenity_name: EntityText = Field(
        description="The specific amenity or feature needed, for example: 'backyard', 'home office', 'hardwood floors', 'updated kitchen', 'central AC', '2-car garage'",
        default=None
    )
    necessity_level: EntityText = Field(
        description="How necessary this amenity is, for example: 'essential', 'must-have', 'important', 'would be great', 'nice to have'",
        default=None
    )


class HasBudgetConstraint(EdgeModel):
    """
    Represents the user's budget limitations and financial constraints for purchasing a property.
    This includes price ranges, maximum prices, and flexibility in the budget.
    """
    min_price: EntityInt = Field(
        description="The minimum price the user is considering in dollars, for example: 200000 or 250000",
        default=None
    )
    max_price: EntityInt = Field(
        description="The maximum price the user can afford in dollars, for example: 400000 or 420000",
        default=None
    )
    flexibility: EntityText = Field(
        description="How flexible the budget is, for example: 'firm', 'can stretch for the right place', 'flexible', 'negotiable'",
        default=None
    )
