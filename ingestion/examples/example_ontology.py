"""A starter ontology for the zep-ingest examples — copy it and adapt.

Setting an ontology BEFORE ingesting matters twice over: it is not retroactive
(data ingested before set_ontology is never re-typed), and it steers extraction
toward a predictable graph shape. Every example passes this spec via the
pipeline's ``ontology=`` preflight so the ordering is impossible to get wrong.

The descriptions below follow the style of Zep's documentation and shipped
default ontology, because descriptions are the primary lever extraction
actually uses:

1. **Entity descriptions state what qualifies, give an ``Examples:`` list, and
   disambiguate against the types they could be confused with** ("Before using
   this classification, first check ..."), exactly as the default ontology does
   for Location/Object/Topic.
2. **Edge descriptions are framed as extractable facts** ("Represents the fact
   that ...") and **enumerate every synonym verb** — extraction matches
   relationships against descriptions, so a verb not named anywhere gets a
   derived type (OWNS, LEADS, ...) instead of your declared one. Adjacent edge
   types (RESPONSIBLE vs WORKS_AT) say what they are NOT so they never compete.
3. **Field descriptions anchor with "for example: ..." and say what to do when
   the value is absent**; all fields are optional (default=None) and avoid the
   reserved attribute names (uuid, name, graph_id, name_embedding, summary,
   created_at).
4. **Signatures are honest.** A declared edge only applies when both endpoints
   classify into its declared source→target pairs — an unclassified entity
   blocks every declared edge that needs it.
5. **Know where the defaults apply.** Zep's default ontology (User, Assistant,
   Preference, Location, Event, Object, Topic, Organization, Document +
   LOCATED_AT/OCCURRED_AT) applies to USER graphs only. On named/business
   graphs — what these examples ingest into — there are NO default types:
   anything you don't declare stays untyped, and untyped entities block every
   declared edge that needs them. That is why this file declares Location and
   LOCATED_AT itself, reusing the default names for consistency. On user
   graphs, custom types are additive to the defaults (a same-name declaration
   overrides classification for that name), and defaults can be disabled with
   ``user.add(disable_default_ontology=True)``.
6. **Start small and iterate.** Zep allows 10 custom entity + 10 custom edge
   types (10 fields each); this uses 5 + 5. After a sample ingest, inspect
   the node labels and edge type names in the Zep dashboard — a long tail of
   derived types (OWNS, LEADS, ...) or untyped entities tells you which
   description or signature to widen next, then re-ingest into a fresh graph.
"""

from pydantic import Field
from zep_cloud import EntityEdgeSourceTarget
from zep_cloud.external_clients.ontology import EdgeModel, EntityModel, EntityText


# In business graphs, people need a declared type: the built-in User type
# represents only the Zep chat user (a singleton), and an unclassified person
# blocks every declared edge that needs a Person endpoint.
class Person(EntityModel):
    """Represents one specific individual human being, referred to by a
    personal name ("Alice Chen", "Bob") — an employee, a customer contact, or
    a supplier contact.
    Examples: an engineering lead, a VP of Sales, a supplier's account
    executive, a customer's operations manager.
    Only individuals with a personal name qualify: roles, teams, and groups
    of people ("shift leads", "support engineers", "mid-market customers")
    are NOT Persons. Never a company, product, or system.
    """

    role: EntityText = Field(
        description=(
            "The person's job title or role, for example: CTO, VP Sales, "
            "Engineering Lead, Account Executive. Leave blank if not stated."
        ),
        default=None,
    )


# Reuses the default ontology's name deliberately (same semantics, consistent
# across graph kinds); on named graphs there is no default Organization, so
# this declaration is what gives the type — and our edge signatures — a home.
class Organization(EntityModel):
    """Represents a company, institution, or formal business entity referred
    to by its proper name ("Voltaic Components").
    Examples: an employer, a customer company, a channel partner, a component
    supplier, a government agency.
    Only named entities qualify: industries, fields, and markets
    ("large-scale automation", "warehouse robotics") are NOT Organizations.
    Named internal initiatives are Projects, not Organizations.
    """

    relationship: EntityText = Field(
        description=(
            "The organization's business relationship to the graph owner, for "
            "example: customer, partner, supplier, vendor. Leave blank if the "
            "relationship is not stated."
        ),
        default=None,
    )


class Project(EntityModel):
    """Represents an internal program, initiative, or workstream with a
    proper name people use to refer to it ("Atlas", "Beacon").
    Examples: a product-development program, a data-migration project, an
    internal platform build.
    Only proper-named initiatives qualify: departments ("the engineering
    organization"), market segments, and kinds of work ("mid-size fulfillment
    operations") are NOT Projects. If customers buy it, it is a Product, not
    a Project. Never a company or a physical part.
    """

    priority: EntityText = Field(
        description=(
            "The project's stated priority designation, for example: P0, P1, "
            "top priority. Leave blank if no priority is stated."
        ),
        default=None,
    )


class Product(EntityModel):
    """Represents a sellable product, SKU, or service offering referred to by
    its brand or model name ("PickPoint One", "FleetView Team") — something
    customers buy, license, subscribe to, or use.
    Examples: a hardware product line, a SaaS subscription plan, a consumer
    app.
    Only branded offerings qualify: generic device categories, descriptions,
    and prototypes ("robotic picking arm", "warehouse robotics", "Atlas
    prototypes") are NOT Products. Internal initiatives are Projects, not
    Products.
    """

    category: EntityText = Field(
        description=(
            "The product's category or family, for example: hardware, software "
            "subscription, professional services. Leave blank if not stated."
        ),
        default=None,
    )


class Location(EntityModel):
    """Represents a physical place where entities exist or activities occur.
    Examples: a city, a region, a plant, an office, a facility, a fulfillment
    or distribution center.
    Never a company (an Organization) and never an event.
    """


class Responsible(EdgeModel):
    """Represents the fact that a person is accountable for a project,
    organization, or product: they own it, lead it, manage it, head it, drive
    it, or are the DRI for it. Use RESPONSIBLE for every phrasing of ownership,
    leadership, management, or accountability — never invent OWNS, LEADS,
    MANAGES, OWNER_OF, or HEAD_OF. Distinct from WORKS_AT, which is plain
    employment with no accountability implied.
    """


class WorksAt(EdgeModel):
    """Represents the fact that a person is employed by or affiliated with an
    organization — plain membership only, for example: works for, is employed
    by, joined, is on the team at. If the person owns, leads, or manages the
    thing, that is RESPONSIBLE, not WORKS_AT.
    """


class Supplies(EdgeModel):
    """Represents the fact that a supplier organization provides components,
    materials, products, or services to another organization or to a project,
    for example: supplies parts to, is a vendor for, manufactures for,
    provides services to. The supplier is the source. Distinct from
    CUSTOMER_OF, which points from a buying organization to its vendor.
    """


class Sells(EdgeModel):
    """Represents the fact that an organization makes and sells a product or
    service offering of its own, for example: sells, offers, ships, launched,
    designs and manufactures. Use SELLS for every phrasing of a company
    offering its own product — never invent HAS_PRODUCT, PRODUCT_OF, or
    OFFERS. Distinct from SUPPLIES (providing components to another
    organization) and CUSTOMER_OF (buying from a vendor).
    """


class CustomerOf(EdgeModel):
    """Represents the fact that an organization is a customer of another
    organization — it buys from, licenses from, subscribes to, pilots,
    renewed with, or is a client of the vendor. The customer is the source
    and the vendor is the target. Use CUSTOMER_OF for every phrasing of a
    customer relationship — never invent CUSTOMER, CLIENT_OF, or
    IS_PILOT_CUSTOMER_FOR. Distinct from SUPPLIES, which points from the
    supplier toward what it supplies.
    """


# Same name as the user-graph default edge, on purpose: named graphs have no
# defaults, so places only connect if we declare the edge ourselves.
class LocatedAt(EdgeModel):
    """Represents the fact that an entity is physically located at, based in,
    installed at, deployed at, or operating in a place, for example: is
    headquartered in, has a facility in, expanded to, was deployed at.
    """


ONTOLOGY = {
    "entities": {
        "Person": Person,
        "Organization": Organization,
        "Project": Project,
        "Product": Product,
        "Location": Location,
    },
    "edges": {
        "RESPONSIBLE": (
            Responsible,
            [
                EntityEdgeSourceTarget(source="Person", target="Project"),
                EntityEdgeSourceTarget(source="Person", target="Organization"),
                EntityEdgeSourceTarget(source="Person", target="Product"),
            ],
        ),
        "WORKS_AT": (
            WorksAt,
            [EntityEdgeSourceTarget(source="Person", target="Organization")],
        ),
        "SUPPLIES": (
            Supplies,
            [
                EntityEdgeSourceTarget(source="Organization", target="Organization"),
                EntityEdgeSourceTarget(source="Organization", target="Project"),
                EntityEdgeSourceTarget(source="Organization", target="Product"),
            ],
        ),
        "SELLS": (
            Sells,
            [EntityEdgeSourceTarget(source="Organization", target="Product")],
        ),
        "CUSTOMER_OF": (
            CustomerOf,
            [EntityEdgeSourceTarget(source="Organization", target="Organization")],
        ),
        "LOCATED_AT": (
            LocatedAt,
            [
                EntityEdgeSourceTarget(source="Organization", target="Location"),
                EntityEdgeSourceTarget(source="Project", target="Location"),
                EntityEdgeSourceTarget(source="Person", target="Location"),
            ],
        ),
    },
}
