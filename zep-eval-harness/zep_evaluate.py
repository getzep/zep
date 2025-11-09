"""
Zep Evaluation Script
Combines graph search, AI response generation, and evaluation into a single pipeline.
"""

import os
import sys
import json
import glob
import asyncio
import statistics
from time import time
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from zep_cloud.client import AsyncZep

# Search configuration
FACTS_LIMIT = 20  # Number of facts (edges) to return
ENTITIES_LIMIT = 10  # Number of entities (nodes) to return
EPISODES_LIMIT = 0  # Number of episodes to return (when enabled)

# LLM Model configuration
LLM_RESPONSE_MODEL = "gpt-5-mini"  # Model used for generating responses
LLM_JUDGE_MODEL = "gpt-4.1"  # Model used for grading responses


# ============================================================================
# Data Models
# ============================================================================


class Grade(BaseModel):
    """Pydantic model for structured LLM grading output."""

    correct: bool = Field(description="True if the answer is correct, False otherwise")
    reasoning: str = Field(
        description="Explain why the answer meets or fails to meet the criteria."
    )


class CompletenessGrade(BaseModel):
    """Pydantic model for evaluating context completeness."""

    completeness: str = Field(description="COMPLETE, PARTIAL, or INSUFFICIENT")
    reasoning: str = Field(
        description="Explain why the context is sufficient or what is missing."
    )
    missing_elements: List[str] = Field(
        default_factory=list, description="List of missing information elements"
    )
    present_elements: List[str] = Field(
        default_factory=list,
        description="List of information elements found in context",
    )


# ============================================================================
# Step 1: Load Run Manifest and Test Cases
# ============================================================================


def get_latest_run() -> Optional[Tuple[int, str]]:
    """
    Get the latest run number and directory.
    Returns tuple of (run_number, run_dir) or None if no runs exist.
    Format: runs/{number}_{ISO8601_timestamp}/
    """
    existing_runs = glob.glob("runs/*")

    if not existing_runs:
        return None

    # Filter out non-directories and .gitkeep
    existing_runs = [r for r in existing_runs if os.path.isdir(r)]

    if not existing_runs:
        return None

    # Sort by directory name (which includes timestamp)
    existing_runs.sort(reverse=True)
    latest_run_dir = existing_runs[0]

    # Extract run number (format: runs/1_timestamp)
    try:
        dir_name = os.path.basename(latest_run_dir)
        run_num = int(dir_name.split("_")[0])
        return run_num, latest_run_dir
    except (IndexError, ValueError):
        return None


def load_run_manifest(run_number: Optional[int] = None) -> Tuple[Dict[str, Any], str]:
    """
    Load the run manifest for evaluation.
    If run_number is None, loads the latest run.
    Returns tuple of (manifest, run_dir).
    """
    if run_number is None:
        result = get_latest_run()
        if result is None:
            raise FileNotFoundError(
                "No runs found in runs/ directory. Please run zep_ingest.py first."
            )
        run_number, run_dir = result
        print(f"Using latest run: #{run_number}")
    else:
        # Find run directory by number (format: runs/{number}_timestamp)
        matching_runs = glob.glob(f"runs/{run_number}_*")
        if not matching_runs:
            raise FileNotFoundError(f"Run #{run_number} not found in runs/ directory.")
        run_dir = matching_runs[0]
        print(f"Using run: #{run_number}")

    manifest_path = os.path.join(run_dir, "manifest.json")
    with open(manifest_path, "r") as f:
        manifest = json.load(f)

    print(f"Loaded manifest from: {manifest_path}")
    print(f"Users: {len(manifest['users'])}")
    print(f"Timestamp: {manifest['timestamp']}\n")

    return manifest, run_dir


async def load_all_test_cases() -> Dict[str, List[Dict[str, Any]]]:
    """
    Load all test case files from data/test_cases/ directory.
    Returns dict mapping user_id to list of test cases.
    """
    test_case_files = glob.glob("data/test_cases/*_tests.json")

    if not test_case_files:
        raise FileNotFoundError("No test case files found in data/test_cases/")

    all_test_cases = {}

    for file_path in test_case_files:
        with open(file_path, "r") as f:
            data = json.load(f)
            user_id = data.get("user_id")
            test_cases = data.get("test_cases", [])

            if user_id and test_cases:
                all_test_cases[user_id] = test_cases

    total_tests = sum(len(tests) for tests in all_test_cases.values())
    print(f"✓ Loaded {total_tests} test case(s) for {len(all_test_cases)} user(s)\n")

    return all_test_cases


# ============================================================================
# Step 2: Graph Search
# ============================================================================


async def perform_graph_search(
    zep_client: AsyncZep, user_id: str, query: str, include_episodes: bool = False
) -> Dict[str, Any]:
    """
    Perform parallel graph search across nodes and edges, optionally including episodes.
    Uses cross-encoder reranker for best accuracy.

    Args:
        zep_client: AsyncZep client instance
        user_id: User ID for graph search
        query: Search query string
        include_episodes: Whether to search episodes (default: False)

    Returns:
        Dictionary containing search results for all scopes
    """
    print(f"Searching [{user_id}]: '{query}'")

    # Search nodes and edges (facts and entities)
    nodes_task = zep_client.graph.search(
        user_id=user_id,
        query=query,
        scope="nodes",
        limit=ENTITIES_LIMIT,
        reranker="cross_encoder",
    )

    edges_task = zep_client.graph.search(
        user_id=user_id,
        query=query,
        scope="edges",
        limit=FACTS_LIMIT,
        reranker="cross_encoder",
    )

    # Optionally search episodes
    if include_episodes:
        episodes_task = zep_client.graph.search(
            user_id=user_id,
            query=query,
            scope="episodes",
            limit=EPISODES_LIMIT,
            reranker="cross_encoder",
        )
        nodes_result, edges_result, episodes_result = await asyncio.gather(
            nodes_task, edges_task, episodes_task
        )
        return {
            "episodes": episodes_result,
            "nodes": nodes_result,
            "edges": edges_result,
        }
    else:
        nodes_result, edges_result = await asyncio.gather(nodes_task, edges_task)
        return {"episodes": None, "nodes": nodes_result, "edges": edges_result}


def construct_context_block(search_results: Dict[str, Any]) -> str:
    """
    Construct a custom context block from graph search results.
    Follows the template format with facts, entities, and optionally episodes.

    Args:
        search_results: Dictionary containing episodes, nodes, and edges

    Returns:
        Formatted context block string for LLM consumption
    """
    context_parts = []

    has_episodes = search_results.get("episodes") is not None

    # Header
    if has_episodes:
        context_parts.append(
            "FACTS, ENTITIES, and EPISODES represent relevant context to the current conversation.\n"
        )
    else:
        context_parts.append(
            "FACTS and ENTITIES represent relevant context to the current conversation.\n"
        )

    # Facts section (edges with temporal validity, labels, and attributes)
    context_parts.append("# These are the most relevant facts")
    context_parts.append('# Facts ending in "present" are currently valid')
    context_parts.append("# Facts with a past end date are NO LONGER VALID.")
    context_parts.append("<FACTS>")

    edges = getattr(search_results["edges"], "edges", [])
    if edges:
        for edge in edges:
            fact = getattr(edge, "fact", "No fact available")
            valid_at = getattr(edge, "valid_at", None)
            invalid_at = getattr(edge, "invalid_at", None)
            labels = getattr(edge, "labels", None)
            attributes = getattr(edge, "attributes", None)

            # Format temporal validity
            valid_at_str = valid_at if valid_at else "unknown"
            invalid_at_str = invalid_at if invalid_at else "present"

            context_parts.append(
                f"{fact} (Date range: {valid_at_str} - {invalid_at_str})"
            )

            # Add labels if present
            if labels and len(labels) > 0:
                context_parts.append(f"  Labels: {', '.join(labels)}")

            # Add attributes if present
            if attributes and isinstance(attributes, dict) and len(attributes) > 0:
                context_parts.append(f"  Attributes:")
                for attr_name, attr_value in attributes.items():
                    context_parts.append(f"    {attr_name}: {attr_value}")

            context_parts.append("")  # Blank line between facts
    else:
        context_parts.append("No relevant facts found")

    context_parts.append("</FACTS>\n")

    # Entities section (nodes with labels and attributes)
    context_parts.append(
        "# These are the most relevant entities (people, locations, organizations, items, and more)."
    )
    context_parts.append("<ENTITIES>")

    nodes = getattr(search_results["nodes"], "nodes", [])
    if nodes:
        for node in nodes:
            name = getattr(node, "name", "Unknown")
            labels = getattr(node, "labels", None)
            attributes = getattr(node, "attributes", None)
            summary = getattr(node, "summary", "No summary available")

            context_parts.append(f"Name: {name}")

            # Add labels if present, filtering out generic "Entity" label when multiple labels exist
            if labels and len(labels) > 0:
                filtered_labels = (
                    [l for l in labels if l != "Entity"] if len(labels) > 1 else labels
                )
                if filtered_labels:
                    context_parts.append(f"Labels: {', '.join(filtered_labels)}")

            # Add attributes if present
            if attributes and isinstance(attributes, dict) and len(attributes) > 0:
                context_parts.append(f"Attributes:")
                for attr_name, attr_value in attributes.items():
                    context_parts.append(f"  {attr_name}: {attr_value}")

            context_parts.append(f"Summary: {summary}")
            context_parts.append("")  # Blank line between entities
    else:
        context_parts.append("No relevant entities found")

    context_parts.append("</ENTITIES>")

    # Episodes section (optional)
    if has_episodes:
        context_parts.append("\n# These are the most relevant episodes")
        context_parts.append("<EPISODES>")

        episodes = getattr(search_results["episodes"], "episodes", [])
        if episodes:
            for episode in episodes:
                content = getattr(episode, "content", "No content available")
                created_at = getattr(episode, "created_at", "Unknown date")
                context_parts.append(f"({created_at}) {content}")
        else:
            context_parts.append("No relevant episodes found")

        context_parts.append("</EPISODES>")

    return "\n".join(context_parts)


# ============================================================================
# Step 3: Generate AI Response
# ============================================================================


def extract_assistant_answer(response) -> str:
    texts = []
    for item in getattr(response, "output", []) or []:
        for block in getattr(item, "content", []) or []:
            if getattr(block, "type", None) == "output_text":
                texts.append(getattr(block, "text", ""))
    return "\n".join(filter(None, texts)).strip()


async def generate_ai_response(
    openai_client: AsyncOpenAI, context: str, question: str
) -> Tuple[str, int]:
    """
    Generate an answer to a question using the provided Zep context.

    Args:
        openai_client: AsyncOpenAI client instance
        context: Retrieved context from Zep graph search
        question: Question to answer

    Returns:
        Tuple of (AI-generated answer string, prompt token count)
    """
    system_prompt = f"""
You are an intelligent AI assistant helping a user with their questions.

You have access to the user's conversation history and relevant information in the CONTEXT.

<CONTEXT>
{context}
</CONTEXT>

Using only the information in the CONTEXT, answer the user's questions. Keep responses SHORT - one sentence when possible.
"""

    response = await openai_client.responses.create(
        model=LLM_RESPONSE_MODEL,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
        reasoning=(
            {"effort": "medium"} if LLM_RESPONSE_MODEL.startswith("gpt-5") else None
        ),
        temperature=0.0 if not LLM_RESPONSE_MODEL.startswith("gpt-5") else None,
    )

    # Extract token usage
    prompt_tokens = response.usage.input_tokens if response.usage else 0

    return extract_assistant_answer(response) or "", prompt_tokens


# ============================================================================
# Step 4: Grade AI Response
# ============================================================================


async def grade_ai_response(
    openai_client: AsyncOpenAI, question: str, golden_answer: str, ai_response: str
) -> Tuple[bool, str]:
    """
    Grade an AI response against golden answer using an LLM judge.

    Args:
        openai_client: AsyncOpenAI client instance
        question: The original question
        golden_answer: The expected correct answer
        ai_response: The AI-generated response to evaluate

    Returns:
        Tuple of (is_correct: bool, reasoning: str)
    """
    system_prompt = """
You are an expert grader that determines if AI responses are correct.
"""

    grading_prompt = f"""
I will give you a question, the golden (correct) answer, and an AI-generated response.

Please evaluate if the response is semantically equivalent to the golden answer. Return true ONLY if the response contains ALL the essential information from the golden answer.

<QUESTION>
{question}
</QUESTION>

<GOLDEN ANSWER>
{golden_answer}
</GOLDEN ANSWER>

<AI RESPONSE>
{ai_response}
</AI RESPONSE>

Evaluation Guidelines:
- The response must contain ALL key information from the golden answer (names, locations, actions, etc.)
- The response doesn't need to match exact wording, but must not omit or change critical details
- If the golden answer specifies a specific name, the response must include that name, not a generic term. 
- Some variation is allowed for commonly acceptable names e.g. NYC or New York may be used to refer to New York City
- If the golden answer includes specific details (location, times, etc.), those must be present
- If the response is missing ANY critical information from the golden answer, return false
- If the response adds conversational filler but contains all essential info, return true
- If the response abstains from answering or says it doesn't know, return false

Examples of INCORRECT responses:
- Golden includes a specific person's name → Response uses a generic role/relationship term instead
- Golden includes a specific location → Response omits the location or uses a generic term
- Golden includes a complete message → Response omits part of the message

Examples of CORRECT responses:
- Golden and response have same key information with different wording
- Golden and response have same key information with different, but commonly acceptable names e.g. NYC or New York may be used to refer to New York City
- Response adds conversational elements but preserves all essential details from golden answer

Please provide your evaluation:
"""

    response = await openai_client.responses.parse(
        model=LLM_JUDGE_MODEL,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": grading_prompt},
        ],
        text_format=Grade,
        reasoning=({"effort": "low"} if LLM_JUDGE_MODEL.startswith("gpt-5") else None),
        temperature=0.0 if not LLM_JUDGE_MODEL.startswith("gpt-5") else None,
    )

    result = response.output_parsed

    return result.correct, result.reasoning


# ============================================================================
# Step 4b: Evaluate Context Completeness (PRIMARY METRIC)
# ============================================================================


async def evaluate_context_completeness(
    openai_client: AsyncOpenAI, question: str, golden_answer: str, context: str
) -> Tuple[str, str, List[str], List[str]]:
    """
    Evaluate whether the retrieved context contains adequate information to answer the question.
    This is the PRIMARY evaluation metric - assessing context quality independent of the AI's answer.

    Args:
        openai_client: AsyncOpenAI client instance
        question: The original question
        golden_answer: The expected answer (used to determine what info is needed)
        context: Retrieved context from Zep graph search

    Returns:
        Tuple of (completeness_grade, reasoning, missing_elements, present_elements)
        where completeness_grade is one of: COMPLETE, PARTIAL, INSUFFICIENT
    """
    system_prompt = """
You are an expert evaluator assessing whether retrieved context contains adequate information to answer a question.
"""

    completeness_prompt = f"""
Your task is to evaluate whether the provided CONTEXT contains sufficient information to answer the QUESTION according to what the GOLDEN ANSWER requires.

IMPORTANT: You are NOT evaluating an answer. You are evaluating whether the CONTEXT itself has the necessary information.

<QUESTION>
{question}
</QUESTION>

<GOLDEN ANSWER>
{golden_answer}
</GOLDEN ANSWER>

<CONTEXT>
{context}
</CONTEXT>

Evaluation Guidelines:

1. **COMPLETE**: The context contains ALL information needed to fully answer the question according to the golden answer.
   - All key elements from the golden answer are present
   - Sufficient detail exists to construct a complete answer
   - Historical facts (with past date ranges) ARE valid context

2. **PARTIAL**: The context contains SOME relevant information but is missing key details.
   - Some elements from the golden answer are present
   - Some critical information is missing or incomplete
   - Additional context would be needed for a complete answer

3. **INSUFFICIENT**: The context lacks most or all critical information needed.
   - Key elements from the golden answer are absent
   - Context is off-topic or irrelevant
   - No reasonable answer could be constructed from this context

IMPORTANT temporal interpretation:
- Facts with date ranges (e.g., "2025-10-01 - 2025-10-07") represent WHEN events occurred
- These historical facts remain VALID context even if dated in the past
- Only mark information as missing if it is truly ABSENT from the context
- Do NOT mark facts as "expired" or "outdated" simply because they have past dates
- Date ranges ending before "present" indicate completed/past events, not invalid information

For your evaluation:
- Identify which information elements ARE present in the context (present_elements)
- Identify which information elements are MISSING (truly absent) from the context (missing_elements)
- Historical facts (past date ranges) count as present information
- Provide clear reasoning explaining your completeness assessment

Please evaluate the context completeness:
"""

    response = await openai_client.responses.parse(
        model=LLM_JUDGE_MODEL,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": completeness_prompt},
        ],
        text_format=CompletenessGrade,
        reasoning=({"effort": "low"} if LLM_JUDGE_MODEL.startswith("gpt-5") else None),
        temperature=0.0 if not LLM_JUDGE_MODEL.startswith("gpt-5") else None,
    )

    result = response.output_parsed
    completeness_grade = result.completeness.strip().upper()

    return (
        completeness_grade,
        result.reasoning,
        result.missing_elements,
        result.present_elements,
    )


# ============================================================================
# Step 5: Process Single Query (Pipeline)
# ============================================================================


async def process_single_query(
    zep_client: AsyncZep,
    openai_client: AsyncOpenAI,
    user_id: str,
    query: str,
    golden_answer: str,
) -> Dict[str, Any]:
    """
    Process a single query through the complete pipeline:
    Search → Evaluate Context Completeness (PRIMARY) → Generate Response → Grade Answer (SECONDARY)

    Args:
        zep_client: AsyncZep client instance
        openai_client: AsyncOpenAI client instance
        user_id: User ID for graph search
        query: Question to answer
        golden_answer: Expected answer for evaluation

    Returns:
        Dictionary containing all results for this query
    """
    start_time = time()

    # Step 1: Search
    search_results = await perform_graph_search(zep_client, user_id, query)
    context = construct_context_block(search_results)
    search_duration_ms = (time() - start_time) * 1000

    # Steps 2 & 3: Run completeness evaluation and response generation in parallel
    completeness_start = time()
    response_start = time()

    # Create coroutines for parallel execution
    completeness_task = evaluate_context_completeness(
        openai_client, query, golden_answer, context
    )
    response_task = generate_ai_response(openai_client, context, query)

    # Execute in parallel
    (completeness_grade, completeness_reasoning, missing_elements, present_elements), (
        ai_answer,
        prompt_tokens,
    ) = await asyncio.gather(completeness_task, response_task)

    completeness_duration_ms = (time() - completeness_start) * 1000
    response_duration_ms = (time() - response_start) * 1000

    # Step 4: Grade Response (SECONDARY METRIC) - must wait for AI answer
    grading_start = time()
    answer_grade, answer_reasoning = await grade_ai_response(
        openai_client, query, golden_answer, ai_answer
    )
    grading_duration_ms = (time() - grading_start) * 1000

    total_duration_ms = (time() - start_time) * 1000

    # Print result with PRIMARY metric first
    completeness_prefix = {
        "COMPLETE": "[✓]",
        "PARTIAL": "[~]",
        "INSUFFICIENT": "[✗]",
    }.get(completeness_grade, "[ ]")

    answer_status = "[✓] CORRECT" if answer_grade else "[✗] WRONG"

    print(f"Question: {query}")
    print(f"  Gold: {golden_answer}")
    print(f"  {completeness_prefix} Context Completeness: {completeness_grade}")
    print(f"     {completeness_reasoning}")
    if missing_elements:
        print(f"     Missing: {', '.join(missing_elements)}")
    print(f"  {answer_status}")
    print(f"     Answer: {ai_answer}")
    print(f"     {answer_reasoning}\n")

    return {
        "question": query,
        "context": context,
        # PRIMARY METRIC: Context Completeness
        "completeness_grade": completeness_grade,
        "completeness_reasoning": completeness_reasoning,
        "completeness_missing_elements": missing_elements,
        "completeness_present_elements": present_elements,
        "completeness_duration_ms": completeness_duration_ms,
        # SECONDARY METRIC: Answer Accuracy
        "answer": ai_answer,
        "golden_answer": golden_answer,
        "answer_grade": answer_grade,
        "answer_reasoning": answer_reasoning,
        # Timing breakdown
        "search_duration_ms": search_duration_ms,
        "response_duration_ms": response_duration_ms,
        "grading_duration_ms": grading_duration_ms,
        "total_duration_ms": total_duration_ms,
        # Token usage
        "response_prompt_tokens": prompt_tokens,
    }


# ============================================================================
# Step 6: Run Complete Evaluation Pipeline
# ============================================================================


async def evaluate_all_questions(
    zep_client: AsyncZep,
    openai_client: AsyncOpenAI,
    manifest: Dict[str, Any],
    test_cases_by_user: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Run the complete evaluation pipeline for all users and their test cases.

    Returns:
        Dictionary mapping user_id to list of evaluation results
    """
    all_results = {}

    # Map base user IDs to actual Zep user IDs
    user_mapping = {}
    for user_data in manifest["users"]:
        base_id = user_data["base_user_id"]
        zep_id = user_data["zep_user_id"]
        user_mapping[base_id] = zep_id

    # Process each user
    for base_user_id, test_cases in test_cases_by_user.items():
        if base_user_id not in user_mapping:
            print(f"Warning: User {base_user_id} not found in manifest, skipping")
            continue

        zep_user_id = user_mapping[base_user_id]
        print(f"\n{'='*80}")
        print(f"Evaluating user: {base_user_id} → {zep_user_id}")
        print(f"Test cases: {len(test_cases)}")
        print(f"{'='*80}\n")

        # Process queries in batches of 5 to avoid overwhelming the API
        batch_size = 15
        user_results = []

        for i in range(0, len(test_cases), batch_size):
            batch = test_cases[i : i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (len(test_cases) + batch_size - 1) // batch_size

            print(
                f"Processing batch {batch_num}/{total_batches} ({len(batch)} queries)..."
            )

            tasks = [
                process_single_query(
                    zep_client,
                    openai_client,
                    zep_user_id,
                    test_case["query"],
                    test_case["golden_answer"],
                )
                for test_case in batch
            ]

            batch_results = await asyncio.gather(*tasks)
            user_results.extend(batch_results)

        all_results[base_user_id] = user_results

        print(f"\n✓ Completed evaluation for user {base_user_id}\n")

    return all_results


# ============================================================================
# Step 6: Save and Analyze Results
# ============================================================================


def calculate_aggregate_statistics(
    results: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """
    Calculate aggregate statistics across all users and per-user statistics.
    Returns structured statistics dictionary.
    """
    # Calculate per-user statistics
    user_scores = {}
    for user_id, user_results in results.items():
        if not user_results:
            continue

        user_total = len(user_results)
        user_complete = sum(
            1 for r in user_results if r["completeness_grade"] == "COMPLETE"
        )
        user_partial = sum(
            1 for r in user_results if r["completeness_grade"] == "PARTIAL"
        )
        user_insufficient = sum(
            1 for r in user_results if r["completeness_grade"] == "INSUFFICIENT"
        )
        user_correct = sum(1 for r in user_results if r["answer_grade"])

        user_scores[user_id] = {
            "total_tests": user_total,
            "completeness": {
                "complete": user_complete,
                "partial": user_partial,
                "insufficient": user_insufficient,
                "complete_rate": (
                    (user_complete / user_total * 100) if user_total > 0 else 0
                ),
                "partial_rate": (
                    (user_partial / user_total * 100) if user_total > 0 else 0
                ),
                "insufficient_rate": (
                    (user_insufficient / user_total * 100) if user_total > 0 else 0
                ),
            },
            "accuracy": {
                "correct": user_correct,
                "incorrect": user_total - user_correct,
                "accuracy_rate": (
                    (user_correct / user_total * 100) if user_total > 0 else 0
                ),
            },
        }

    # Calculate aggregate statistics across all users
    all_user_results = []
    for user_results in results.values():
        all_user_results.extend(user_results)

    total_questions = len(all_user_results)

    if total_questions == 0:
        return {"user_scores": user_scores, "aggregate_scores": {}}

    # Completeness metrics
    complete_count = sum(
        1 for r in all_user_results if r["completeness_grade"] == "COMPLETE"
    )
    partial_count = sum(
        1 for r in all_user_results if r["completeness_grade"] == "PARTIAL"
    )
    insufficient_count = sum(
        1 for r in all_user_results if r["completeness_grade"] == "INSUFFICIENT"
    )

    complete_rate = complete_count / total_questions * 100
    partial_rate = partial_count / total_questions * 100
    insufficient_rate = insufficient_count / total_questions * 100

    # Accuracy metrics
    correct_answer_count = sum(1 for r in all_user_results if r["answer_grade"])
    answer_accuracy = correct_answer_count / total_questions * 100

    # Timing statistics - all four metrics
    total_durations = [r["total_duration_ms"] for r in all_user_results]
    search_durations = [r["search_duration_ms"] for r in all_user_results]
    completeness_durations = [r["completeness_duration_ms"] for r in all_user_results]
    grading_durations = [r["grading_duration_ms"] for r in all_user_results]

    if total_questions > 1:
        median_total = statistics.median(total_durations)
        stdev_total = statistics.stdev(total_durations)
        median_search = statistics.median(search_durations)
        stdev_search = statistics.stdev(search_durations)
        median_completeness = statistics.median(completeness_durations)
        stdev_completeness = statistics.stdev(completeness_durations)
        median_grading = statistics.median(grading_durations)
        stdev_grading = statistics.stdev(grading_durations)
    else:
        median_total = total_durations[0]
        stdev_total = 0
        median_search = search_durations[0]
        stdev_search = 0
        median_completeness = completeness_durations[0]
        stdev_completeness = 0
        median_grading = grading_durations[0]
        stdev_grading = 0

    # Token statistics
    prompt_tokens_list = [r["response_prompt_tokens"] for r in all_user_results]
    total_prompt_tokens = sum(prompt_tokens_list)

    if total_questions > 1:
        median_prompt_tokens = statistics.median(prompt_tokens_list)
        stdev_prompt_tokens = statistics.stdev(prompt_tokens_list)
    else:
        median_prompt_tokens = prompt_tokens_list[0]
        stdev_prompt_tokens = 0

    # Correlation analysis
    complete_and_correct = sum(
        1
        for r in all_user_results
        if r["completeness_grade"] == "COMPLETE" and r["answer_grade"]
    )
    complete_but_wrong = sum(
        1
        for r in all_user_results
        if r["completeness_grade"] == "COMPLETE" and not r["answer_grade"]
    )

    aggregate_scores = {
        "total_tests": total_questions,
        "completeness": {
            "complete": complete_count,
            "partial": partial_count,
            "insufficient": insufficient_count,
            "complete_rate": complete_rate,
            "partial_rate": partial_rate,
            "insufficient_rate": insufficient_rate,
        },
        "accuracy": {
            "correct": correct_answer_count,
            "incorrect": total_questions - correct_answer_count,
            "accuracy_rate": answer_accuracy,
        },
        "timing": {
            "total_median_ms": median_total,
            "total_stdev_ms": stdev_total,
            "search_median_ms": median_search,
            "search_stdev_ms": stdev_search,
            "grading_median_ms": median_grading,
            "grading_stdev_ms": stdev_grading,
            "completeness_median_ms": median_completeness,
            "completeness_stdev_ms": stdev_completeness,
        },
        "tokens": {
            "prompt_median": median_prompt_tokens,
            "prompt_stdev": stdev_prompt_tokens,
            "total_prompt": total_prompt_tokens,
        },
        "correlation": {
            "complete_and_correct": complete_and_correct,
            "complete_but_wrong": complete_but_wrong,
            "complete_total": complete_count,
            "accuracy_when_complete": (
                (complete_and_correct / complete_count * 100)
                if complete_count > 0
                else 0
            ),
        },
    }

    return {"user_scores": user_scores, "aggregate_scores": aggregate_scores}


def save_results(
    results: Dict[str, List[Dict[str, Any]]], run_dir: str, manifest: Dict[str, Any]
):
    """
    Save evaluation results with comprehensive aggregate statistics to JSON file.
    """
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    results_file = os.path.join(run_dir, f"evaluation_results_{timestamp}.json")

    # Calculate statistics
    stats = calculate_aggregate_statistics(results)

    # Prepare output structure
    output_data = {
        "evaluation_timestamp": timestamp,
        "run_number": manifest.get("run_number"),
        "search_configuration": {
            "facts_limit": FACTS_LIMIT,
            "entities_limit": ENTITIES_LIMIT,
            "episodes_limit": EPISODES_LIMIT,
        },
        "model_configuration": {
            "response_model": LLM_RESPONSE_MODEL,
            "judge_model": LLM_JUDGE_MODEL,
        },
        "aggregate_scores": stats["aggregate_scores"],
        "user_scores": stats["user_scores"],
        "detailed_results": results,
    }

    with open(results_file, "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"\n{'='*80}")
    print(f"Results saved to: {results_file}")
    print(f"{'='*80}")

    return results_file, stats


def print_summary(stats: Dict[str, Any]):
    """
    Print summary statistics for the evaluation.
    """
    aggregate = stats["aggregate_scores"]
    user_scores = stats["user_scores"]

    if not aggregate:
        print("No results to summarize")
        return

    total_tests = aggregate["total_tests"]

    print(f"\n{'='*80}")
    print(f"AGGREGATE SCORES ({total_tests} total tests)")
    print(f"{'='*80}\n")

    # PRIMARY METRIC - Context Completeness
    print("PRIMARY METRIC - Context Completeness:")
    print(
        f"  COMPLETE:     {aggregate['completeness']['complete']:3d} / {total_tests} ({aggregate['completeness']['complete_rate']:.1f}%)"
    )
    print(
        f"  PARTIAL:      {aggregate['completeness']['partial']:3d} / {total_tests} ({aggregate['completeness']['partial_rate']:.1f}%)"
    )
    print(
        f"  INSUFFICIENT: {aggregate['completeness']['insufficient']:3d} / {total_tests} ({aggregate['completeness']['insufficient_rate']:.1f}%)"
    )

    # SECONDARY METRIC - Answer Accuracy
    print(f"\nSECONDARY METRIC - Answer Accuracy:")
    print(
        f"  CORRECT:   {aggregate['accuracy']['correct']:3d} / {total_tests} ({aggregate['accuracy']['accuracy_rate']:.1f}%)"
    )
    print(f"  INCORRECT: {aggregate['accuracy']['incorrect']:3d} / {total_tests}")

    # Correlation Analysis
    print(f"\nCorrelation Analysis:")
    corr = aggregate["correlation"]
    if corr["complete_total"] > 0:
        print(
            f"  When context is COMPLETE: {corr['complete_and_correct']}/{corr['complete_total']} answers correct ({corr['accuracy_when_complete']:.1f}%)"
        )
    print(
        f"  Complete but wrong: {corr['complete_but_wrong']}/{corr['complete_total']}"
    )

    # Timing
    print(f"\nTiming:")
    print(
        f"  Total time per query:     {aggregate['timing']['total_median_ms']:.0f} ± {aggregate['timing']['total_stdev_ms']:.0f}ms"
    )
    print(
        f"  Search time:              {aggregate['timing']['search_median_ms']:.0f} ± {aggregate['timing']['search_stdev_ms']:.0f}ms"
    )
    print(
        f"  Accuracy eval:            {aggregate['timing']['grading_median_ms']:.0f} ± {aggregate['timing']['grading_stdev_ms']:.0f}ms"
    )
    print(
        f"  Completeness eval:        {aggregate['timing']['completeness_median_ms']:.0f} ± {aggregate['timing']['completeness_stdev_ms']:.0f}ms"
    )

    # Token Usage
    print(f"\nToken Usage:")
    print(
        f"  Prompt tokens per query: {aggregate['tokens']['prompt_median']:.0f} ± {aggregate['tokens']['prompt_stdev']:.0f}"
    )
    print(f"  Total prompt tokens:     {aggregate['tokens']['total_prompt']}")

    # Per-User Scores
    print(f"\n\n{'='*80}")
    print("PER-USER SCORES")
    print(f"{'='*80}\n")

    for user_id, scores in user_scores.items():
        print(f"User: {user_id} ({scores['total_tests']} tests)")
        print("-" * 80)
        print(
            f"  Completeness: COMPLETE={scores['completeness']['complete_rate']:.1f}%, "
            f"PARTIAL={scores['completeness']['partial_rate']:.1f}%, "
            f"INSUFFICIENT={scores['completeness']['insufficient_rate']:.1f}%"
        )
        print(
            f"  Accuracy:     {scores['accuracy']['accuracy_rate']:.1f}% "
            f"({scores['accuracy']['correct']}/{scores['total_tests']} correct)"
        )
        print()


# ============================================================================
# Main Function
# ============================================================================


async def main():
    # Load environment variables
    load_dotenv()

    # Parse command-line arguments
    run_number = None
    if len(sys.argv) > 1:
        try:
            run_number = int(sys.argv[1])
        except ValueError:
            print(f"Error: Invalid run number '{sys.argv[1]}'")
            print("Usage: python zep_evaluate.py [run_number]")
            exit(1)

    # Validate environment variables
    zep_api_key = os.getenv("ZEP_API_KEY")
    openai_api_key = os.getenv("OPENAI_API_KEY")

    if not zep_api_key:
        print("Error: Missing ZEP_API_KEY environment variable")
        exit(1)

    if not openai_api_key:
        print("Error: Missing OPENAI_API_KEY environment variable")
        exit(1)

    # Initialize clients
    zep_client = AsyncZep(api_key=zep_api_key)
    openai_client = AsyncOpenAI(api_key=openai_api_key)

    print("=" * 80)
    print("ZEP EVALUATION SCRIPT")
    print("=" * 80)

    try:
        # Load run manifest
        manifest, run_dir = load_run_manifest(run_number)

        # Load test cases
        test_cases_by_user = await load_all_test_cases()

        # Run evaluation
        print("Starting evaluation...\n")
        results = await evaluate_all_questions(
            zep_client, openai_client, manifest, test_cases_by_user
        )

        # Save results with aggregate statistics
        results_file, stats = save_results(results, run_dir, manifest)

        # Print summary
        print_summary(stats)

        print(f"\n{'='*80}")
        print("EVALUATION COMPLETE")
        print(f"{'='*80}")
        print(f"\nDetailed results saved to: {results_file}")

    except Exception as e:
        print(f"\nEvaluation failed: {e}")
        import traceback

        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    asyncio.run(main())
