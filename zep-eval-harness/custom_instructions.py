"""
Zep Custom Instructions

This module defines two sets of custom instructions:

1. **User custom instructions** — for user conversation graphs (personal assistants).
   Applied via --custom-instructions flag.
2. **Document custom instructions** — for standalone document graphs (reference material).
   Applied via --document-custom-instructions flag.

Custom instructions describe the domain, terminology, and conventions of your
application so Zep can better understand and interpret data during graph extraction.

See: https://help.getzep.com/customizing-graph-structure#custom-instructions
"""

from zep_cloud import CustomInstruction


# ============================================================================
# User Instruction Definitions
# ============================================================================

CUSTOM_INSTRUCTIONS = [
    CustomInstruction(
        name="conversation_context",
        text=(
            "These are personal conversations between a user and an AI assistant. "
            "Pay attention to real-world entities: people, places, organizations, "
            "events, and items mentioned by the user."
        ),
    ),
    CustomInstruction(
        name="temporal_reasoning",
        text=(
            "Conversations span multiple sessions over time. Track temporal "
            "references carefully — resolve relative expressions like 'last week' "
            "or 'tomorrow' against the message timestamp."
        ),
    ),
    CustomInstruction(
        name="preference_sensitivity",
        text=(
            "Extract user preferences with high sensitivity. Statements like "
            "'I love sushi', 'I hate running', or 'I prefer mornings' indicate "
            "preferences that should be captured as relationships."
        ),
    ),
]

# Instruction names for manifest logging
INSTRUCTION_NAMES = [i.name for i in CUSTOM_INSTRUCTIONS]


# ============================================================================
# User Instructions Setup Function
# ============================================================================


async def set_custom_instructions(zep_client, user_ids=None):
    """
    Set custom instructions for user graph extraction.

    Custom instructions describe your domain — terminology, concepts, and
    conventions — so Zep can better understand and interpret data during
    extraction.

    Args:
        zep_client: AsyncZep client instance
        user_ids: Optional list of user IDs to apply to.
                 If None, applies project-wide.

    Example usage:
        ```python
        from zep_cloud import AsyncZep
        client = AsyncZep(api_key="your-key")
        await set_custom_instructions(client, user_ids=["user_123"])
        ```
    """
    kwargs = {"instructions": CUSTOM_INSTRUCTIONS}
    if user_ids:
        kwargs["user_ids"] = user_ids

    await zep_client.graph.add_custom_instructions(**kwargs)


# ============================================================================
# Document Instruction Definitions
# ============================================================================

DOCUMENT_CUSTOM_INSTRUCTIONS = [
    CustomInstruction(
        name="document_extraction",
        text=(
            "This is reference documentation — not a personal conversation. "
            "Focus on extracting factual information: definitions, specifications, "
            "processes, and relationships between concepts, components, and topics."
        ),
    ),
    CustomInstruction(
        name="structure_preservation",
        text=(
            "Preserve the hierarchical structure of the document. Track which "
            "concepts belong to which topics, which components are part of larger "
            "systems, and how processes relate to specifications."
        ),
    ),
    CustomInstruction(
        name="cross_reference_tracking",
        text=(
            "Track cross-references between document sections and concepts. "
            "When a chunk references another concept, specification, or component "
            "defined elsewhere, capture that relationship explicitly."
        ),
    ),
]

# Document instruction names for manifest logging
DOCUMENT_INSTRUCTION_NAMES = [i.name for i in DOCUMENT_CUSTOM_INSTRUCTIONS]


# ============================================================================
# Document Instructions Setup Function
# ============================================================================


async def set_document_custom_instructions(zep_client, graph_ids=None):
    """
    Set custom instructions for standalone document graph extraction.

    These instructions are tailored for reference material — focusing on
    factual extraction, structure preservation, and cross-referencing rather
    than personal preferences or relationships.

    Args:
        zep_client: AsyncZep client instance
        graph_ids: Optional list of graph IDs to apply to.
                  If None, applies project-wide.

    Example usage:
        ```python
        from zep_cloud import AsyncZep
        client = AsyncZep(api_key="your-key")
        await set_document_custom_instructions(client, graph_ids=["my_doc_graph"])
        ```
    """
    kwargs = {"instructions": DOCUMENT_CUSTOM_INSTRUCTIONS}
    if graph_ids:
        kwargs["graph_ids"] = graph_ids

    await zep_client.graph.add_custom_instructions(**kwargs)
