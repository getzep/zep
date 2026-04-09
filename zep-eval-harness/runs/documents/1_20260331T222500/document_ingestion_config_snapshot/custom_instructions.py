"""
Zep Custom Instructions — Document Graphs

Custom instructions for standalone document graphs.
These describe the domain, terminology, and conventions so Zep can better
understand and interpret data during graph extraction.

See: https://help.getzep.com/custom-instructions
"""

from zep_cloud import CustomInstruction


# ============================================================================
# Instruction Definitions
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
# Setup Function
# ============================================================================


async def set_document_custom_instructions(zep_client, graph_ids=None):
    """
    Set custom instructions for standalone document graph extraction.

    Args:
        zep_client: AsyncZep client instance
        graph_ids: Optional list of graph IDs to apply to.
                  If None, applies project-wide.
    """
    kwargs = {"instructions": DOCUMENT_CUSTOM_INSTRUCTIONS}
    if graph_ids:
        kwargs["graph_ids"] = graph_ids

    await zep_client.graph.add_custom_instructions(**kwargs)
