"""
Zep Evaluation Script
Combines graph search, AI response generation, and evaluation into a single pipeline.
"""

import os
import json
import asyncio
from time import time
from typing import List, Dict, Any, Tuple

import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from zep_cloud.client import AsyncZep

# User ID from ingestion script
USER_ID = "zep_eval_test_user_001"

# Search configuration
SEARCH_LIMIT = 10  # Number of results per scope

# LLM Model configuration
LLM_RESPONSE_MODEL = "gpt-5-mini"  # Model used for generating responses
LLM_JUDGE_MODEL = "gpt-5-mini"      # Model used for grading responses


# ============================================================================
# Data Models
# ============================================================================

class Grade(BaseModel):
    """Pydantic model for structured LLM grading output."""
    is_correct: str = Field(description='CORRECT or WRONG')
    reasoning: str = Field(description='Explain why the answer meets or fails to meet the criteria.')


# ============================================================================
# Step 1: Load Test Questions
# ============================================================================

async def load_test_questions() -> pd.DataFrame:
    """Load test questions from CSV file."""
    csv_file = "data/test_questions.csv"

    if not os.path.exists(csv_file):
        raise FileNotFoundError(f"Test questions file not found: {csv_file}")

    df = pd.read_csv(csv_file)
    print(f"✅ Loaded {len(df)} test question(s) from {csv_file}\n")

    return df


# ============================================================================
# Step 2: Graph Search
# ============================================================================

async def perform_graph_search(
    zep_client: AsyncZep,
    query: str
) -> Dict[str, Any]:
    """
    Perform parallel graph search across episodes, nodes, and edges.
    Uses cross-encoder reranker for best accuracy.

    Args:
        zep_client: AsyncZep client instance
        query: Search query string

    Returns:
        Dictionary containing search results for all scopes
    """
    print(f"🔍 Searching: '{query}'")

    # Search in parallel across all three scopes
    episodes_task = zep_client.graph.search(
        user_id=USER_ID,
        query=query,
        scope="episodes",
        limit=SEARCH_LIMIT,
        reranker="cross_encoder"
    )

    nodes_task = zep_client.graph.search(
        user_id=USER_ID,
        query=query,
        scope="nodes",
        limit=SEARCH_LIMIT,
        reranker="cross_encoder"
    )

    edges_task = zep_client.graph.search(
        user_id=USER_ID,
        query=query,
        scope="edges",
        limit=SEARCH_LIMIT,
        reranker="cross_encoder"
    )

    # Execute all searches in parallel
    episodes_result, nodes_result, edges_result = await asyncio.gather(
        episodes_task, nodes_task, edges_task
    )

    return {
        "episodes": episodes_result,
        "nodes": nodes_result,
        "edges": edges_result
    }


def construct_context_block(search_results: Dict[str, Any]) -> str:
    """
    Construct a custom context block from graph search results.

    Args:
        search_results: Dictionary containing episodes, nodes, and edges

    Returns:
        Formatted context block string for LLM consumption
    """
    context_parts = []

    context_parts.append("# RETRIEVED CONTEXT FROM ZEP GRAPH SEARCH\n")

    # Episodes section
    context_parts.append("## Episodes (Conversation Segments):")
    episodes = getattr(search_results["episodes"], 'episodes', [])
    if episodes:
        for episode in episodes:
            content = getattr(episode, 'content', 'No content available')
            created_at = getattr(episode, 'created_at', 'Unknown date')
            context_parts.append(f"- ({created_at}) {content}")
    else:
        context_parts.append("- No relevant episodes found")

    # Edges section (facts with temporal validity)
    context_parts.append("\n## Facts (Edges):")
    edges = getattr(search_results["edges"], 'edges', [])
    if edges:
        for edge in edges:
            fact = getattr(edge, 'fact', 'No fact available')
            valid_at = getattr(edge, 'valid_at', None)
            invalid_at = getattr(edge, 'invalid_at', None)

            # Format temporal validity
            valid_at_str = valid_at if valid_at else "unknown date"
            invalid_at_str = invalid_at if invalid_at else "present"

            context_parts.append(f"- {fact} (Valid: {valid_at_str} to {invalid_at_str})")
    else:
        context_parts.append("- No relevant facts found")

    # Nodes section (entities)
    context_parts.append("\n## Entities (Nodes):")
    nodes = getattr(search_results["nodes"], 'nodes', [])
    if nodes:
        for node in nodes:
            name = getattr(node, 'name', 'Unknown')
            summary = getattr(node, 'summary', 'No summary available')
            context_parts.append(f"- {name}: {summary}")
    else:
        context_parts.append("- No relevant entities found")

    return "\n".join(context_parts)


# ============================================================================
# Step 3: Generate AI Response
# ============================================================================

async def generate_ai_response(
    openai_client: AsyncOpenAI,
    context: str,
    question: str
) -> str:
    """
    Generate an answer to a question using the provided Zep context.

    Args:
        openai_client: AsyncOpenAI client instance
        context: Retrieved context from Zep graph search
        question: Question to answer

    Returns:
        AI-generated answer string
    """
    system_prompt = """
You are a helpful expert assistant answering questions based on the provided context from a knowledge graph.
"""

    prompt = f"""
# CONTEXT:
You have access to facts, entities, and conversation episodes from Zep's knowledge graph.

Your task is to briefly answer the question based on the context provided. If you don't know how to answer the question, abstain from answering.

<CONTEXT>
{context}
</CONTEXT>

<QUESTION>
{question}
</QUESTION>

# APPROACH (Think step by step):
1. First, examine all memories/facts that contain information related to the question
2. Examine the timestamps and content of these memories carefully
3. Look for explicit mentions of dates, times, locations, or events that answer the question
4. If the answer requires calculation (e.g., converting relative time references), show your work
5. Formulate a precise, concise answer based solely on the evidence in the context
6. Double-check that your answer directly addresses the question asked

Context:

{context}

Question: {question}
Answer:
"""

    response = await openai_client.chat.completions.create(
        model=LLM_RESPONSE_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        reasoning_effort="minimal",
    )

    return response.choices[0].message.content or ''


# ============================================================================
# Step 4: Grade AI Response
# ============================================================================

async def grade_ai_response(
    openai_client: AsyncOpenAI,
    question: str,
    golden_answer_criteria: str,
    ai_response: str
) -> Tuple[bool, str]:
    """
    Grade an AI response against golden answer criteria using an LLM judge.

    Args:
        openai_client: AsyncOpenAI client instance
        question: The original question
        golden_answer_criteria: Criteria for what a correct answer should contain
        ai_response: The AI-generated response to evaluate

    Returns:
        Tuple of (is_correct: bool, reasoning: str)
    """
    system_prompt = """
You are an expert grader that determines if AI responses meet specified criteria.
"""

    grading_prompt = f"""
I will give you a question, criteria for what a correct answer should contain, and an AI-generated response.

Please evaluate if the response meets the criteria. Answer "CORRECT" if the response satisfies the criteria, otherwise answer "WRONG".

<QUESTION>
{question}
</QUESTION>

<GOLDEN ANSWER CRITERIA>
{golden_answer_criteria}
</GOLDEN ANSWER CRITERIA>

<AI RESPONSE>
{ai_response}
</AI RESPONSE>

Evaluation Guidelines:
- The response must satisfy the key requirements specified in the criteria
- The response doesn't need to match exact wording, but must convey the required information
- If the response contains the essential information, even if worded differently, it should be marked CORRECT
- If the response is missing critical information specified in the criteria, it should be marked WRONG
- If the response abstains from answering or says it doesn't know, it should be marked WRONG

Please provide your evaluation:
"""

    response = await openai_client.beta.chat.completions.parse(
        model=LLM_JUDGE_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": grading_prompt}
        ],
        response_format=Grade,
        reasoning_effort="minimal",
    )

    result = response.choices[0].message.parsed
    is_correct = result.is_correct.strip().upper() == 'CORRECT'

    return is_correct, result.reasoning


# ============================================================================
# Step 5: Process Single Query (Pipeline)
# ============================================================================

async def process_single_query(
    zep_client: AsyncZep,
    openai_client: AsyncOpenAI,
    query: str,
    golden_answer_criteria: str
) -> Dict[str, Any]:
    """
    Process a single query through the complete pipeline:
    Search → Generate Response → Grade

    Args:
        zep_client: AsyncZep client instance
        openai_client: AsyncOpenAI client instance
        query: Question to answer
        golden_answer_criteria: Criteria for evaluation

    Returns:
        Dictionary containing all results for this query
    """
    start_time = time()

    # Step 1: Search
    search_results = await perform_graph_search(zep_client, query)
    context = construct_context_block(search_results)

    # Step 2: Generate Response
    ai_answer = await generate_ai_response(openai_client, context, query)

    # Step 3: Grade Response
    is_correct, reasoning = await grade_ai_response(
        openai_client, query, golden_answer_criteria, ai_answer
    )

    duration_ms = (time() - start_time) * 1000

    # Print result
    status = "✅ CORRECT" if is_correct else "❌ WRONG"
    print(f"{status}: {query}")
    print(f"   Answer: {ai_answer}")
    print(f"   Reasoning: {reasoning}\n")

    return {
        "question": query,
        "context": context,
        "answer": ai_answer,
        "golden_answer_criteria": golden_answer_criteria,
        "grade": is_correct,
        "reasoning": reasoning,
        "duration_ms": duration_ms
    }


# ============================================================================
# Main Execution
# ============================================================================

async def main():
    """Main execution function - runs the complete evaluation pipeline."""
    # Load environment variables
    load_dotenv()

    # Validate environment variables
    zep_api_key = os.getenv("ZEP_API_KEY")
    openai_api_key = os.getenv("OPENAI_API_KEY")

    if not zep_api_key:
        print("❌ Error: Missing ZEP_API_KEY environment variable")
        exit(1)
    if not openai_api_key:
        print("❌ Error: Missing OPENAI_API_KEY environment variable")
        exit(1)

    # Initialize clients
    zep_client = AsyncZep(api_key=zep_api_key)
    openai_client = AsyncOpenAI(api_key=openai_api_key)

    print("=" * 80)
    print("ZEP EVALUATION SCRIPT")
    print("Pipeline: Search → Generate Response → Grade")
    print("=" * 80)
    print(f"\n🔑 User ID: {USER_ID}")
    print(f"🔍 Search limit per scope: {SEARCH_LIMIT}")
    print(f"🎯 Reranker: cross_encoder")
    print(f"🤖 Response Model: {LLM_RESPONSE_MODEL}")
    print(f"⚖️  Judge Model: {LLM_JUDGE_MODEL}\n")

    try:
        # Load test questions
        test_questions_df = await load_test_questions()

        print(f"🚀 Starting evaluation for {len(test_questions_df)} queries...\n")

        # Process all queries in parallel
        tasks = [
            process_single_query(
                zep_client,
                openai_client,
                row['query'],
                row['golden_answer_criteria']
            )
            for _, row in test_questions_df.iterrows()
        ]

        results = await asyncio.gather(*tasks)

        # Calculate statistics
        total_questions = len(results)
        correct_count = sum(1 for r in results if r['grade'])
        accuracy = (correct_count / total_questions * 100) if total_questions > 0 else 0
        avg_duration_ms = sum(r['duration_ms'] for r in results) / total_questions if total_questions > 0 else 0

        # Save detailed results
        os.makedirs("data", exist_ok=True)

        # Save all intermediate data for debugging
        output_data = {
            USER_ID: results
        }

        with open("data/zep_evaluation_results.json", "w") as f:
            json.dump(output_data, f, indent=2)

        # Print summary
        print("=" * 80)
        print("EVALUATION COMPLETE ✅")
        print("=" * 80)
        print(f"\n📊 Results:")
        print(f"   Total questions: {total_questions}")
        print(f"   Correct answers: {correct_count}")
        print(f"   Wrong answers: {total_questions - correct_count}")
        print(f"   Accuracy: {accuracy:.1f}%")
        print(f"   Average time per query: {avg_duration_ms:.0f}ms")
        print(f"\n💾 Detailed results saved to: data/zep_evaluation_results.json")

    except Exception as e:
        print(f"\n❌ Evaluation failed: {e}")
        exit(1)


if __name__ == "__main__":
    asyncio.run(main())
