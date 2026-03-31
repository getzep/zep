"""
Zep Custom Instructions

This module defines two sets of custom instructions:

1. **User custom instructions** — for user conversation graphs (personal assistants).
   Applied via --custom-instructions flag.
2. **Document custom instructions** — for standalone document graphs (reference material).
   Applied via --custom-instructions flag in zep_ingest_documents.py.

Custom instructions describe the domain, terminology, and conventions of your
application so Zep can better understand and interpret data during graph extraction.

See: https://help.getzep.com/custom-instructions
"""

from zep_cloud import CustomInstruction


# ============================================================================
# User Instruction Definitions
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
        name="real_estate_reference_domain",
        text=(
            "These documents are reference guides for residential real estate "
            "buyers. Key terminology includes: conventional loan (a mortgage not "
            "backed by a government agency), FHA loan (Federal Housing "
            "Administration-backed loan with lower down payment requirements), "
            "VA loan (Veterans Affairs loan with no down payment), PMI (Private "
            "Mortgage Insurance, required when down payment is below 20%), and "
            "APR (Annual Percentage Rate, the total yearly cost of borrowing)."
        ),
    ),
    CustomInstruction(
        name="home_buying_process",
        text=(
            "Documents describe the home buying process from search to closing. "
            "Key concepts include: home inspection (a professional assessment of "
            "a property's condition), title search (verification of legal "
            "ownership), escrow (a neutral third party holding funds during "
            "closing), and earnest money deposit (buyer's good-faith payment). "
            "An HOA (Homeowners Association) governs community rules and charges "
            "dues. CC&Rs are Covenants, Conditions, and Restrictions that HOAs "
            "enforce."
        ),
    ),
    CustomInstruction(
        name="financial_concepts",
        text=(
            "Documents cover financial topics relevant to home buyers. Key terms: "
            "down payment (upfront cash payment, typically 3-20% of purchase price), "
            "debt-to-income ratio or DTI (monthly debt payments divided by gross "
            "income, used by lenders to assess affordability), credit score "
            "(numerical rating of creditworthiness, typically 300-850), and "
            "amortization (the schedule of loan payments over time splitting "
            "principal and interest)."
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
