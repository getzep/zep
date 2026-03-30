"""
Zep User Summary Instructions

User summary instructions customize how Zep generates the entity summary for
each user's node in their knowledge graph. Up to 5 instructions per user.
Each instruction consists of a name (unique identifier) and text (max 100 chars).

This follows the same pattern as ontology.py — define instructions here and
apply them during ingestion via the --user-summary-instructions flag.

See: https://help.getzep.com/user-summary-instructions
"""

from zep_cloud.types import UserInstruction


# ============================================================================
# Instruction Definitions
# ============================================================================

USER_SUMMARY_INSTRUCTIONS = [
    UserInstruction(
        name="personal_interests",
        text="What are the user's hobbies, interests, and favorite activities?",
    ),
    UserInstruction(
        name="relationships",
        text="Who are the important people in the user's life and what are those relationships?",
    ),
    UserInstruction(
        name="life_events",
        text="What significant life events or milestones has the user experienced?",
    ),
    UserInstruction(
        name="preferences",
        text="What are the user's stated preferences, likes, and dislikes?",
    ),
    UserInstruction(
        name="daily_life",
        text="What does the user's daily routine, work, or living situation look like?",
    ),
]

# Instruction names for manifest logging
INSTRUCTION_NAMES = [i.name for i in USER_SUMMARY_INSTRUCTIONS]


# ============================================================================
# Setup Function
# ============================================================================


async def set_user_summary_instructions(zep_client, user_ids=None):
    """
    Set user summary instructions for user node summary generation.

    User summary instructions customize what information Zep extracts for
    each user's summary on their user node in the knowledge graph.

    Args:
        zep_client: AsyncZep client instance
        user_ids: Optional list of user IDs to apply to.
                 If None, applies project-wide.

    Example usage:
        ```python
        from zep_cloud import AsyncZep
        client = AsyncZep(api_key="your-key")
        await set_user_summary_instructions(client, user_ids=["user_123"])
        ```
    """
    kwargs = {"instructions": USER_SUMMARY_INSTRUCTIONS}
    if user_ids:
        kwargs["user_ids"] = user_ids

    await zep_client.user.add_user_summary_instructions(**kwargs)
