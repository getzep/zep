"""Graph ontology for the Pemberline Medical example.

The entity and edge types below are the schema the application defines for the
graph. The system prompt renders the same schema as text so the model knows
which labels and edge types it can use in tools.
"""

from __future__ import annotations

from zep_cloud.external_clients.ontology import EdgeModel, EntityModel, EntityText
from zep_cloud.external_clients.ontology import Field as OntologyField
from zep_cloud.types import EntityEdgeSourceTarget


class Employee(EntityModel):
    """A person who works at the company."""

    title: EntityText = OntologyField(description="Job title", default=None)
    team: EntityText = OntologyField(description="Team name", default=None)


class Team(EntityModel):
    """A team or department in the company."""


class Product(EntityModel):
    """A medical device product that the company sells or plans to sell."""

    category: EntityText = OntologyField(
        description="Product category, for example infusion pump", default=None
    )
    lifecycle_status: EntityText = OntologyField(
        description="on market, pre-launch, or discontinued", default=None
    )
    launch_date: EntityText = OntologyField(
        description="Planned or actual launch date, YYYY-MM-DD", default=None
    )


class Component(EntityModel):
    """A part used in a product, identified by a part number."""

    part_number: EntityText = OntologyField(description="Part number, such as FS-20", default=None)


class Supplier(EntityModel):
    """A company that supplies components."""


class Site(EntityModel):
    """A manufacturing plant."""

    country: EntityText = OntologyField(
        description="Country where the plant is located", default=None
    )


class QualityIssue(EntityModel):
    """A product quality problem tracked with an identifier such as QI-1041. A report that mentions an issue is a Report, not a new QualityIssue."""

    issue_id: EntityText = OntologyField(
        description="Quality issue identifier, such as QI-1041", default=None
    )
    severity: EntityText = OntologyField(
        description="high, medium, or low; use the latest value", default=None
    )
    status: EntityText = OntologyField(description="open or closed", default=None)
    capa_id: EntityText = OntologyField(
        description="Linked CAPA identifier, such as CAPA-118", default=None
    )


class RegulatoryFiling(EntityModel):
    """A regulatory submission or certificate for one product in one region, such as a 510(k) or an MDR CE certificate. A report about a filing is a Report, not a RegulatoryFiling."""

    region: EntityText = OntologyField(description="US or EU", default=None)
    filing_type: EntityText = OntologyField(
        description="510(k) or MDR CE certificate", default=None
    )
    status: EntityText = OntologyField(description="cleared, valid, pending, expired", default=None)
    key_date: EntityText = OntologyField(
        description="Most recent decision or status date, YYYY-MM-DD", default=None
    )


class Customer(EntityModel):
    """A hospital or health system that buys or uses the company's products."""

    segment: EntityText = OntologyField(description="Customer segment", default=None)
    country: EntityText = OntologyField(
        description="Country where the customer is located", default=None
    )


class Report(EntityModel):
    """A dated company document identified by a report ID such as R10. Examples are field service reports, CAPA records, audit reports, complaint summaries, engineering tests, regulatory updates, risk reviews, sales notes, and marketing notes. A report is not a regulatory filing, a quality issue, or a person."""

    report_id: EntityText = OntologyField(
        description="Report identifier, such as R10", default=None
    )
    report_type: EntityText = OntologyField(
        description="audit_report, capa_record, complaint_summary, engineering_test, field_service_report, marketing_note, regulatory_update, risk_review, or sales_note",
        default=None,
    )
    report_date: EntityText = OntologyField(description="Report date, YYYY-MM-DD", default=None)


ENTITY_TYPES: dict[str, type[EntityModel]] = {
    "Employee": Employee,
    "Team": Team,
    "Product": Product,
    "Component": Component,
    "Supplier": Supplier,
    "Site": Site,
    "QualityIssue": QualityIssue,
    "RegulatoryFiling": RegulatoryFiling,
    "Customer": Customer,
    "Report": Report,
}

EDGE_TYPES: dict[str, tuple[type[EdgeModel], list[EntityEdgeSourceTarget]]] = {
    "MEMBER_OF": (
        type("MemberOf", (EdgeModel,), {"__doc__": "The employee is a member of the team."}),
        [EntityEdgeSourceTarget(source="Employee", target="Team")],
    ),
    "REPORTS_TO": (
        type(
            "ReportsTo",
            (EdgeModel,),
            {"__doc__": "The source employee reports to the target employee (manager)."},
        ),
        [EntityEdgeSourceTarget(source="Employee", target="Employee")],
    ),
    "WORKS_ON": (
        type("WorksOn", (EdgeModel,), {"__doc__": "The employee works on the product."}),
        [EntityEdgeSourceTarget(source="Employee", target="Product")],
    ),
    "OWNS": (
        type(
            "Owns",
            (EdgeModel,),
            {"__doc__": "The employee owns the issue, its CAPA, or the filing."},
        ),
        [
            EntityEdgeSourceTarget(source="Employee", target="QualityIssue"),
            EntityEdgeSourceTarget(source="Employee", target="RegulatoryFiling"),
        ],
    ),
    "CONTAINS": (
        type("Contains", (EdgeModel,), {"__doc__": "The product uses the component."}),
        [EntityEdgeSourceTarget(source="Product", target="Component")],
    ),
    "SUPPLIES": (
        type("Supplies", (EdgeModel,), {"__doc__": "The supplier supplies the component."}),
        [EntityEdgeSourceTarget(source="Supplier", target="Component")],
    ),
    "MANUFACTURED_AT": (
        type(
            "ManufacturedAt",
            (EdgeModel,),
            {"__doc__": "The product is assembled at the site."},
        ),
        [EntityEdgeSourceTarget(source="Product", target="Site")],
    ),
    "AFFECTS": (
        type(
            "Affects",
            (EdgeModel,),
            {"__doc__": "The quality issue affects the product or component."},
        ),
        [
            EntityEdgeSourceTarget(source="QualityIssue", target="Product"),
            EntityEdgeSourceTarget(source="QualityIssue", target="Component"),
        ],
    ),
    "FILED_FOR": (
        type("FiledFor", (EdgeModel,), {"__doc__": "The filing is for the product."}),
        [EntityEdgeSourceTarget(source="RegulatoryFiling", target="Product")],
    ),
    "REPORTED": (
        type(
            "Reported",
            (EdgeModel,),
            {"__doc__": "The customer reported the quality issue."},
        ),
        [EntityEdgeSourceTarget(source="Customer", target="QualityIssue")],
    ),
}

ENTITY_LABELS = list(ENTITY_TYPES)
EDGE_TYPE_NAMES = list(EDGE_TYPES)


def render_entity_types() -> str:
    """Render the entity types as `- Name: description` lines for the prompt."""
    lines = []
    for name, model in ENTITY_TYPES.items():
        lines.append(f"- {name}: {model.__doc__.strip()}")
    return "\n".join(lines)


def render_edge_types() -> str:
    """Render the edge types as `- NAME (Source -> Target): description` lines."""
    lines = []
    for name, (model, source_targets) in EDGE_TYPES.items():
        pairs = ", ".join(f"{st.source} -> {st.target}" for st in source_targets)
        lines.append(f"- {name} ({pairs}): {model.__doc__.strip()}")
    return "\n".join(lines)
