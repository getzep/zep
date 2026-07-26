"""
Search Result Formatting

Turns Zep search results into the text lines used by both retrieval paths:
the deterministic context block (`construct_context_block()` in
`zep_evaluate.py`) and the agent's retrieval tools (`tools.py`).

Edit these functions to change how facts, entities, and episodes are rendered
for the response model.
"""


def format_edges(edges) -> list[str]:
    """Format a list of edge (fact) results into context lines."""
    parts = []
    if edges:
        for edge in edges:
            fact = getattr(edge, "fact", "No fact available")
            valid_at = getattr(edge, "valid_at", None)
            invalid_at = getattr(edge, "invalid_at", None)
            labels = getattr(edge, "labels", None)
            attributes = getattr(edge, "attributes", None)

            valid_at_str = valid_at if valid_at else "unknown"
            invalid_at_str = invalid_at if invalid_at else "present"

            parts.append(f"{fact} (Date range: {valid_at_str} - {invalid_at_str})")

            if labels and len(labels) > 0:
                parts.append(f"  Labels: {', '.join(labels)}")

            if attributes and isinstance(attributes, dict) and len(attributes) > 0:
                parts.append(f"  Attributes:")
                for attr_name, attr_value in attributes.items():
                    parts.append(f"    {attr_name}: {attr_value}")

            parts.append("")
    else:
        parts.append("No relevant facts found")
    return parts


def format_nodes(nodes) -> list[str]:
    """Format a list of node (entity) results into context lines."""
    parts = []
    if nodes:
        for node in nodes:
            name = getattr(node, "name", "Unknown")
            labels = getattr(node, "labels", None)
            attributes = getattr(node, "attributes", None)
            summary = getattr(node, "summary", "No summary available")

            parts.append(f"Name: {name}")

            if labels and len(labels) > 0:
                filtered_labels = (
                    [l for l in labels if l != "Entity"] if len(labels) > 1 else labels
                )
                if filtered_labels:
                    parts.append(f"Labels: {', '.join(filtered_labels)}")

            if attributes and isinstance(attributes, dict) and len(attributes) > 0:
                parts.append(f"Attributes:")
                for attr_name, attr_value in attributes.items():
                    parts.append(f"  {attr_name}: {attr_value}")

            parts.append(f"Summary: {summary}")
            parts.append("")
    else:
        parts.append("No relevant entities found")
    return parts


def format_episodes(episodes) -> list[str]:
    """Format a list of episode (raw data) results into context lines."""
    parts = []
    if episodes:
        for episode in episodes:
            content = getattr(episode, "content", "No content available")
            created_at = getattr(episode, "created_at", "Unknown date")
            parts.append(f"({created_at}) {content}")
    else:
        parts.append("No relevant episodes found")
    return parts
