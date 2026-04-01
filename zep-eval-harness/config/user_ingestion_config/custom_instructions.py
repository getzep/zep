"""
Zep Custom Instructions — User Graphs

Custom instructions for user conversation graphs.
These describe the domain, terminology, and conventions so Zep can better
understand and interpret data during graph extraction.

See: https://help.getzep.com/custom-instructions
"""

from zep_cloud import CustomInstruction


# ============================================================================
# Instruction Definitions
# ============================================================================

CUSTOM_INSTRUCTIONS = [
    CustomInstruction(
        name="real_estate_domain",
        text=(
            "This application operates in the residential real estate domain. "
            "Users are home buyers working with an AI assistant to find properties. "
            "Key terminology includes: listing (a property available for sale), "
            "closing (the final transaction transferring ownership), pre-approval "
            "(a lender's conditional commitment to a loan amount), earnest money "
            "(a deposit demonstrating buyer intent), contingency (a condition that "
            "must be met before closing), and appraisal (a professional property "
            "valuation). A 'budget' refers to the buyer's maximum purchase price."
        ),
    ),
    CustomInstruction(
        name="property_and_location",
        text=(
            "Users discuss specific neighborhoods, cities, and school districts "
            "when evaluating where to buy. Common property attributes include: "
            "bedrooms, bathrooms, square footage, lot size, and home office space. "
            "A 'floor plan' refers to the layout of rooms. 'HOA' (Homeowners "
            "Association) is an organization that manages a community and charges "
            "monthly fees. 'Remote work' or 'hybrid work' affects home office needs."
        ),
    ),
    CustomInstruction(
        name="household_context",
        text=(
            "Users mention household members — spouses, children, and pets — whose "
            "needs influence property requirements. Family size determines bedroom "
            "count, children's ages affect school district priority, and pets may "
            "require fenced yards or breed-friendly HOA policies. Professional "
            "context (employer, commute, remote work schedule) shapes location "
            "preferences and home office requirements."
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
    Set custom instructions for user graph extraction.

    Args:
        zep_client: AsyncZep client instance
        user_ids: Optional list of user IDs to apply to.
                 If None, applies project-wide.
    """
    kwargs = {"instructions": CUSTOM_INSTRUCTIONS}
    if user_ids:
        kwargs["user_ids"] = user_ids

    await zep_client.graph.add_custom_instructions(**kwargs)
