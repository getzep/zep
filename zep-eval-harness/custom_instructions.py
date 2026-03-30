"""
Zep Custom Instructions

Custom instructions describe the domain, terminology, and conventions of your
application so Zep can better understand and interpret data during graph extraction.

This follows the same pattern as ontology.py — define instructions here and
apply them during ingestion via the --custom-instructions flag.

See: https://help.getzep.com/customizing-graph-structure#custom-instructions
"""

from zep_cloud import CustomInstruction


# ============================================================================
# Instruction Definitions
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
# Setup Function
# ============================================================================


async def set_custom_instructions(zep_client, user_ids=None):
    """
    Set custom instructions for graph extraction.

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
