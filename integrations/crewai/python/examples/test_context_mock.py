"""Demonstration of context data that CrewAI agents receive from Zep storage"""

import logging
from unittest.mock import MagicMock

from zep_crewai import ZepGraphStorage, ZepUserStorage

# Set up logging to see the context
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def demonstrate_user_context():
    """Show what context looks like from user storage"""

    logger.info("\n" + "=" * 60)
    logger.info("USER STORAGE CONTEXT DEMONSTRATION")
    logger.info("=" * 60)

    # Mock Zep client - bypass type checking
    from zep_cloud import Zep

    mock_zep = MagicMock(spec=Zep)
    # ``MagicMock(spec=Zep)`` only knows class-level attributes; the Zep client
    # assigns ``.thread`` in ``__init__`` (instance attribute), so attach it
    # explicitly before configuring its return value.
    mock_zep.thread = MagicMock()

    # Example 1: Context Block from the user graph
    logger.info("\n1. CONTEXT BLOCK - thread.get_user_context(thread_id)")
    logger.info("-" * 60)

    # Mock the Context Block response (Zep V3 auto-assembles this; no 'mode' arg)
    mock_context_summary = MagicMock()
    mock_context_summary.context = """User Profile Summary:
- Name: Sarah Chen
- Location: Seattle, WA
- Interests: Coffee culture, local roasters, specialty drinks

Recent Conversation Topics:
- Discussed favorite coffee shops in Seattle
- Mentioned preference for Victrola Coffee Roasters
- Interested in exploring new local coffee spots
- Enjoys light roast single-origin beans

Key Facts:
- Regular coffee drinker (2-3 cups daily)
- Prefers pour-over brewing method
- Has visited 15+ coffee shops in Seattle area
- Works in tech industry downtown"""

    mock_zep.thread.get_user_context = MagicMock(return_value=mock_context_summary)

    # Create storage
    storage_summary = ZepUserStorage(
        client=mock_zep,
        user_id="user_123",
        thread_id="thread_456",
    )

    # Get context - this calls thread.get_user_context(thread_id)
    context = storage_summary.get_context()
    logger.info("Context that CrewAI agent receives:")
    logger.info(context)

    # Example 2: a more conversational Context Block
    logger.info("\n2. CONVERSATIONAL CONTEXT BLOCK")
    logger.info("-" * 60)

    mock_context_raw = MagicMock()
    mock_context_raw.context = """Thread Messages:

[2024-01-15 10:00] User: I love exploring new coffee shops in Seattle. Do you have any recommendations?

[2024-01-15 10:01] Assistant: Seattle has an amazing coffee culture! What kind of atmosphere do you prefer - cozy and quiet, or bustling and social?

[2024-01-15 10:02] User: I prefer cozy spots with good pour-over options. My favorite so far is Victrola Coffee Roasters.

[2024-01-15 10:03] Assistant: If you like Victrola, you'd probably enjoy Analog Coffee in Capitol Hill. They have excellent pour-over and a similar cozy vibe.

[2024-01-15 10:04] User: Thanks! I'll check it out this weekend. I usually get their Ethiopian single-origin beans."""

    mock_zep.thread.get_user_context = MagicMock(return_value=mock_context_raw)

    storage_raw = ZepUserStorage(client=mock_zep, user_id="user_123", thread_id="thread_456")

    context = storage_raw.get_context()
    logger.info("Context that CrewAI agent receives:")
    logger.info(context)

    # Example 3: How a CrewAI agent uses this context
    logger.info("\n3. CREWAI AGENT USING ZEP CONTEXT")
    logger.info("-" * 60)

    logger.info("When a CrewAI agent processes a task, you:")
    logger.info("1. Call storage.get_context() to retrieve the Context Block")
    logger.info("2. Inject it into the agent/task prompt to personalize the response")
    logger.info("3. Or expose a ZepSearchTool so the agent can search on demand")

    logger.info("\nExample task: 'Recommend a new coffee shop for the user'")
    logger.info("\nThe agent sees this context and can make personalized recommendations")
    logger.info("based on the user's preferences (Victrola, pour-over, light roast, etc.)")


def demonstrate_graph_context():
    """Show what context looks like from graph storage"""

    logger.info("\n" + "=" * 60)
    logger.info("GRAPH STORAGE CONTEXT DEMONSTRATION")
    logger.info("=" * 60)

    # Mock Zep client and graph
    from zep_cloud import Zep

    mock_zep = MagicMock(spec=Zep)
    mock_graph = MagicMock()
    mock_zep.graph = mock_graph

    # Mock a single combined graph.search result (edges/nodes/episodes are read
    # off the same GraphSearchResults object per scope).
    mock_results = MagicMock()
    mock_results.edges = [
        MagicMock(
            fact="Optimal water temperature for coffee brewing is 195-205°F",
            valid_at="2024-01-10",
            invalid_at=None,
        ),
        MagicMock(
            fact="V60 dripper creates clean, bright coffee with paper filters",
            valid_at="2024-01-10",
            invalid_at=None,
        ),
    ]
    mock_results.nodes = [
        MagicMock(
            name="Victrola Coffee",
            summary="Local Seattle roaster known for direct trade relationships",
        ),
    ]
    mock_results.episodes = [
        MagicMock(
            content="Customer visited Victrola Coffee and tried their new Ethiopian Yirgacheffe.",
        )
    ]

    mock_graph.search = MagicMock(return_value=mock_results)

    # Create storage
    storage = ZepGraphStorage(
        client=mock_zep, graph_id="coffee_knowledge_graph", facts_limit=20, entity_limit=5
    )

    # Search the graph for a query (returns composed context)
    results = storage.search("coffee brewing techniques")

    logger.info("\nContext assembled from knowledge graph:")
    logger.info("-" * 60)
    logger.info(results)

    logger.info("\nThis context includes:")
    logger.info("- Related facts from graph edges (relationships between concepts)")
    logger.info("- Entity information from nodes (key concepts and their facts)")
    logger.info("- Relevant episodes (specific events or interactions)")
    logger.info("\nCrewAI agents use this rich context to provide informed responses")


def demonstrate_search_filters():
    """Show how search filters work"""

    logger.info("\n" + "=" * 60)
    logger.info("SEARCH FILTERS DEMONSTRATION")
    logger.info("=" * 60)

    logger.info("\nSearch filters allow you to constrain what data is searched:")
    logger.info("""
    SearchFilters(
        # Scope can limit search to specific parts of the graph
        scope='edges',  # Only search edges (relationships)
        scope='nodes',  # Only search nodes (entities)
        scope='episodes',  # Only search episodes (events)

        # Date filters for temporal constraints
        start_date='2024-01-01',
        end_date='2024-12-31',

        # Metadata filters for custom constraints
        metadata_filter={
            'location': 'Seattle',
            'category': 'coffee'
        }
    )
    """)

    logger.info("These filters help agents find the most relevant context for their tasks")


if __name__ == "__main__":
    demonstrate_user_context()
    demonstrate_graph_context()
    demonstrate_search_filters()

    logger.info("\n" + "=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info("""
CrewAI agents receive context from Zep in these ways:

1. User Storage (ZepUserStorage):
   - Retrieves user-specific context from conversation threads
   - Returns Zep's auto-assembled Context Block (Zep V3 removed the 'mode' option)
   - Context includes user profile, preferences, conversation history
   - Directly uses thread.get_user_context() from Zep SDK

2. Graph Storage (ZepGraphStorage):
   - Searches knowledge graphs for relevant information
   - Combines edges (relationships), nodes (entities), and episodes (events)
   - Uses compose_context_string() to format the context
   - Supports parallel search across different graph components

3. Search Filters:
   - Allow precise control over what data is searched
   - Support temporal and metadata-based filtering
   - Help agents find the most relevant information

The context is automatically injected into the agent's prompt,
allowing for personalized and informed responses.
    """)
