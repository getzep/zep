"""
Zep User Summary Instructions

User summary instructions customize how Zep generates the entity summary for
each user's node in their knowledge graph. Up to 5 instructions per user.
Each instruction consists of a name (unique identifier, max 100 chars) and text (max 100 chars).

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
        name="property_requirements",
        text="What are the user's property requirements (bedrooms, bathrooms, office, yard)?",
    ),
    UserInstruction(
        name="budget_and_finances",
        text="What is the user's budget, down payment situation, and mortgage pre-approval status?",
    ),
    UserInstruction(
        name="location_preferences",
        text="What cities, neighborhoods, or school districts is the user interested in, and why?",
    ),
    UserInstruction(
        name="household_composition",
        text="Who is in the user's household (spouse, children, pets) and how does it affect the search?",
    ),
    UserInstruction(
        name="work_and_lifestyle",
        text="What is the user's work situation (remote/hybrid/onsite, commute) and its effect on preferences?",
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
