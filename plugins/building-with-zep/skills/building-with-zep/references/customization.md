# Customizing Zep: ontology, instructions, manual facts

Customization improves extraction quality and enables precise retrieval — but it is prompt engineering. **Start simple and iterate**; don't model everything up front.

## Custom ontology (entity & edge types)

Defines the *types* your graph should contain — the entities (nouns) and relationships (verbs) that matter in your domain. Recommended for most production use cases: it focuses extraction and lets you filter retrieval by type.

### Define types

```python
from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel
from pydantic import Field

class Restaurant(EntityModel):
    """Represents a specific restaurant."""
    cuisine_type: EntityText = Field(description="The cuisine type", default=None)

class RestaurantVisit(EdgeModel):
    """The fact that the user visited a restaurant."""
    restaurant_name: EntityText = Field(description="Name of the restaurant", default=None)
```

TS uses `entityFields.text(...)` with `EntityType`/`EdgeType`; Go uses `zep.BaseEntity`/`zep.BaseEdge` struct tags.

### Apply

```python
from zep_cloud import EntityEdgeSourceTarget

client.graph.set_ontology(
    entities={"Restaurant": Restaurant},
    edges={"RESTAURANT_VISIT": (RestaurantVisit,
            [EntityEdgeSourceTarget(source="User", target="Restaurant")])},
    # scope: omit user_ids/graph_ids = project-wide; provide them = per user/graph
)
```

`set_ontology` **overwrites** the previous set — the active types are always those from the last call. Scope per user/graph with `user_ids=[...]` / `graph_ids=[...]`.

### Default ontology

User graphs apply Zep defaults unless disabled. Entity types: `User`, `Assistant`, `Preference`, `Location`, `Event`, `Object`, `Topic`, `Organization`, `Document`. Edge types: `LOCATED_AT`, `OCCURRED_AT`. Disable with `client.user.add(..., disable_default_ontology=True)` (or `user.update`).

### Limits & rules

- Max **10 entity types** and **10 edge types** per scope; ≤10 fields each.
- A custom type must define ≥1 custom property.
- Reserved attribute names (don't use): `uuid`, `name`, `graph_id`, `name_embedding`, `summary`, `created_at`.
- Each node/edge gets a **single** type — no multi-classification.
- Changing types does **not** reclassify existing nodes/edges.

### Good for / not good for

- **Good for:** focusing extraction on domain-critical entities/relationships; type-filtered retrieval (`types=[...]`, `node_labels`, `edge_types`); better results than defaults alone in most production apps.
- **Not good for / cautions:** modeling everything up front. Start with a few generic types and minimal fields, then add complexity. Custom **attributes** are an advanced "precision context engineering" feature — many use cases are fully served by node summaries + facts with no attributes. Design entity types as nouns, edge types as relationships, or a type may end up on the wrong element. Resolve overlapping types (e.g. "Hobby" vs "Hiking") by stating priority in the descriptions.

### Retrieve from custom types

Template: `%{entities types=[Restaurant] limit=5}`, `%{edges types=[RESTAURANT_VISIT] limit=10}`. Or search: `graph.search(..., scope="nodes", search_filters={"node_labels": ["Restaurant"]})`.

## Custom instructions (Enterprise)

Describe the *domain itself* — terminology and concepts Zep wouldn't otherwise know — so it interprets data better during extraction. Applied **automatically** on every ingest to the target graph (no per-call parameter).

```python
from zep_cloud import CustomInstruction
client.graph.add_custom_instructions(
    instructions=[CustomInstruction(name="legal_domain",
        text="This app operates in the legal domain. Terms include: estoppel, tort, indemnification...")],
    # scope with user_ids / graph_ids; omit for project-wide
)
```

- Resolution order: graph-specific → project-wide default → built-in logic.
- Re-adding a name **updates** that instruction (upsert).
- Limits: 5 instructions/request, name ≤100 chars, text 1–5,000 chars, ≤50 user/graph IDs per request.
- **Good for:** specialized vocabulary (legal, healthcare, internal jargon). **Not for:** defining entity/edge *types* — that's ontology. Ontology = the *shape*; instructions = how to *interpret* the domain.

## User summary instructions

Steer what the always-on user summary captures (lives on the user node). Up to 5 per user/set/project-wide; `name` + `text` (≤100 chars).

```python
from zep_cloud.types import UserInstruction
client.user.add_user_summary_instructions(
    instructions=[UserInstruction(name="professional_background",
        text="What are the user's key professional skills and achievements?")],
    user_ids=[user_id],   # omit for project-wide defaults
)
# list_user_summary_instructions(user_id=...), delete_user_summary_instructions(instruction_names=[...], user_ids=[...])
```

Defaults cover personal details, relationships, work, preferences/goals, and AI-assistance preferences. Best practice: focused, question-shaped prompts answerable in a sentence or two. **Limitation:** they don't apply to Batch-API-ingested data.

## Manual fact triplets

Insert a fact directly when you already know it (async — returns `task_id`):

```python
result = client.graph.add_fact_triple(
    user_id=user_id, fact="Paul met Eric", fact_name="MET",
    source_node_name="Paul", target_node_name="Eric Clapton",
)
```

Optionally attach `source_node_labels` / `target_node_labels`, `*_node_attributes`, `edge_attributes`, `valid_at`/`invalid_at`, `metadata`. If a label/`fact_name` matches an ontology type *with* defined properties, attributes are validated strictly (HTTP 400 on mismatch); otherwise they pass through. Limits: `fact` ≤250 chars, `fact_name` ≤50 chars SCREAMING_SNAKE_CASE, node names ≤50 chars.

## Pattern detection (experimental)

Analyze graph structure for recurring patterns — relationships, multi-hop paths, co-occurrences, hubs, clusters. Seed mode (explicit labels/edge types) or query mode (natural language, relationship patterns only).

```python
result = client.graph.detect_patterns(user_id="alice", seeds={"node_labels": ["Decision"]})
for p in result.patterns:
    print(p.type, p.description, p.occurrences)
```

Useful for graph auditing, schema discovery, and data-quality checks — not part of the normal retrieval path.
