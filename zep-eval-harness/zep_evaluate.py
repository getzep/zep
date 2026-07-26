"""
Zep Evaluation Script
Combines graph search, AI response generation, and evaluation into a single pipeline.
"""

import os
import sys
import json
import glob
import shutil
import asyncio
import argparse
import statistics
from collections import defaultdict
from time import time
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from zep_cloud.client import AsyncZep

from config.constants import GEMINI_BASE_URL
from config.evaluation_config.constants import (
    CONTEXT_BLOCK_RERANKER,
    DOC_ENTITIES_LIMIT,
    DOC_EPISODES_LIMIT,
    DOC_FACTS_LIMIT,
    LLM_JUDGE_MODEL,
    LLM_RESPONSE_MODEL,
    MAX_TOOL_CALLS,
    MAX_TOOL_ITERATIONS,
    REQUIRE_TOOL_CALL,
    SEARCH_MAX_QUERY_CHARS,
    SUPPORTED_RERANKERS,
    TOOL_SEARCH_DEFAULT_LIMIT,
    TOOL_SEARCH_MAX_CHARACTERS,
    TOOL_SEARCH_MAX_LIMIT,
    TOOL_SEARCH_RERANKER,
    USER_ENTITIES_LIMIT,
    USER_EPISODES_LIMIT,
    USER_FACTS_LIMIT,
    USE_CONTEXT_BLOCK,
    USE_TOOLS,
)
from config.evaluation_config.formatting import (
    format_edges,
    format_episodes,
    format_nodes,
)
from config.evaluation_config.judge_prompts import (
    get_accuracy_judge_prompts,
    get_completeness_judge_prompts,
)
from config.evaluation_config.response_prompt import (
    get_response_system_prompt,
    get_tool_agent_system_prompt,
)
from config.evaluation_config.tools import TOOL_SPECS
from retry import retry_with_backoff
import tool_agent
from tool_agent import AgentLoopResult, ToolContext, ToolRegistry, run_tool_agent


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


def get_latest_run(run_type: str) -> Optional[Tuple[int, str]]:
    """
    Get the latest run number and directory for a given run type.
    run_type is "users" or "documents".
    Returns tuple of (run_number, run_dir) or None if no runs exist.
    Format: runs/{run_type}/{number}_{ISO8601_timestamp}/
    """
    existing_runs = glob.glob(f"runs/{run_type}/*")

    if not existing_runs:
        return None

    # Filter out non-directories and .gitkeep
    existing_runs = [r for r in existing_runs if os.path.isdir(r)]

    if not existing_runs:
        return None

    # Sort by run number (not lexicographic — avoids 9 > 10 bug)
    def extract_run_num(path):
        try:
            return int(os.path.basename(path).split("_")[0])
        except (IndexError, ValueError):
            return -1

    existing_runs.sort(key=extract_run_num, reverse=True)
    latest_run_dir = existing_runs[0]
    run_num = extract_run_num(latest_run_dir)
    if run_num < 0:
        return None
    return run_num, latest_run_dir


def load_run_manifest(run_number: Optional[int], run_type: str) -> Tuple[Dict[str, Any], str]:
    """
    Load a run manifest for evaluation.
    run_type is "users" or "documents".
    If run_number is None, loads the latest run of that type.
    Returns tuple of (manifest, run_dir).
    """
    if run_number is None:
        result = get_latest_run(run_type)
        if result is None:
            raise FileNotFoundError(
                f"No {run_type} runs found in runs/{run_type}/ directory. "
                f"Please run zep_ingest_{run_type}.py first."
            )
        run_number, run_dir = result
        print(f"Using latest {run_type} run: #{run_number}")
    else:
        # Find run directory by number
        matching_runs = glob.glob(f"runs/{run_type}/{run_number}_*")
        if not matching_runs:
            raise FileNotFoundError(
                f"{run_type.capitalize()} run #{run_number} not found in runs/{run_type}/ directory."
            )
        run_dir = matching_runs[0]
        print(f"Using {run_type} run: #{run_number}")

    manifest_path = os.path.join(run_dir, "manifest.json")
    with open(manifest_path, "r") as f:
        manifest = json.load(f)

    print(f"Loaded manifest from: {manifest_path}")
    if run_type == "users":
        print(f"Users: {len(manifest['users'])}")
    elif run_type == "documents":
        print(f"Document graph: {manifest.get('graph_id', 'N/A')}")
        print(f"Chunks: {manifest.get('num_chunks', 0)}")
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
    zep_client: AsyncZep,
    user_id: str,
    query: str,
    include_episodes: bool = False,
    doc_graph_id: str | None = None,
) -> Dict[str, Any]:
    """
    Perform parallel graph search across the user graph and optionally a
    standalone document graph. All searches run concurrently.

    Args:
        zep_client: AsyncZep client instance
        user_id: User ID for user graph search
        query: Search query string
        include_episodes: Whether to search episodes (default: False)
        doc_graph_id: If provided, also search this standalone graph

    Returns:
        Dictionary containing search results for all scopes
    """
    print(f"Searching [{user_id}]: '{query}'")

    # Zep rejects a query longer than this, so a verbose test question would fail
    # the whole search rather than retrieve from its first characters. The tools
    # truncate identically, keeping the two retrieval paths comparable.
    query = query[:SEARCH_MAX_QUERY_CHARS]

    # Build all search tasks for maximum parallelism
    tasks: dict[str, Any] = {}

    # User graph searches
    tasks["user_nodes"] = retry_with_backoff(
        zep_client.graph.search,
        user_id=user_id,
        query=query,
        scope="nodes",
        limit=USER_ENTITIES_LIMIT,
        reranker=CONTEXT_BLOCK_RERANKER,
        description=f"search user nodes [{user_id}]",
    )
    tasks["user_edges"] = retry_with_backoff(
        zep_client.graph.search,
        user_id=user_id,
        query=query,
        scope="edges",
        limit=USER_FACTS_LIMIT,
        reranker=CONTEXT_BLOCK_RERANKER,
        description=f"search user edges [{user_id}]",
    )
    if include_episodes:
        tasks["user_episodes"] = retry_with_backoff(
            zep_client.graph.search,
            user_id=user_id,
            query=query,
            scope="episodes",
            limit=USER_EPISODES_LIMIT,
            reranker=CONTEXT_BLOCK_RERANKER,
            description=f"search user episodes [{user_id}]",
        )

    # Standalone document graph searches (if enabled)
    if doc_graph_id:
        tasks["doc_nodes"] = retry_with_backoff(
            zep_client.graph.search,
            graph_id=doc_graph_id,
            query=query,
            scope="nodes",
            limit=DOC_ENTITIES_LIMIT,
            reranker=CONTEXT_BLOCK_RERANKER,
            description=f"search doc nodes [{doc_graph_id}]",
        )
        tasks["doc_edges"] = retry_with_backoff(
            zep_client.graph.search,
            graph_id=doc_graph_id,
            query=query,
            scope="edges",
            limit=DOC_FACTS_LIMIT,
            reranker=CONTEXT_BLOCK_RERANKER,
            description=f"search doc edges [{doc_graph_id}]",
        )
        if include_episodes:
            tasks["doc_episodes"] = retry_with_backoff(
                zep_client.graph.search,
                graph_id=doc_graph_id,
                query=query,
                scope="episodes",
                limit=DOC_EPISODES_LIMIT,
                reranker=CONTEXT_BLOCK_RERANKER,
                description=f"search doc episodes [{doc_graph_id}]",
            )

    # Execute all searches in parallel
    keys = list(tasks.keys())
    results_list = await asyncio.gather(*tasks.values())
    results = dict(zip(keys, results_list))

    return {
        # User graph results
        "nodes": results["user_nodes"],
        "edges": results["user_edges"],
        "episodes": results.get("user_episodes"),
        # Document graph results (None if not searched)
        "doc_nodes": results.get("doc_nodes"),
        "doc_edges": results.get("doc_edges"),
        "doc_episodes": results.get("doc_episodes"),
    }


def construct_context_block(
    search_results: Dict[str, Any],
    user_summary: str | None = None,
) -> str:
    """
    Construct a context block from graph search results.
    Includes user summary, user graph results, and optionally document graph results.

    Args:
        search_results: Dictionary containing user and document graph results
        user_summary: Optional user summary from the user node

    Returns:
        Formatted context block string for LLM consumption
    """
    context_parts = []

    has_episodes = search_results.get("episodes") is not None
    has_doc_results = search_results.get("doc_edges") is not None

    # User summary
    if user_summary:
        context_parts.append("# High-level summary of the user")
        context_parts.append("<USER_SUMMARY>")
        context_parts.append(user_summary)
        context_parts.append("</USER_SUMMARY>\n")

    # --- User graph results ---
    context_parts.append(
        "FACTS, ENTITIES,"
        + (" and EPISODES " if has_episodes else " ")
        + "represent relevant context from the user's knowledge graph.\n"
    )

    # Facts
    context_parts.append("# These are the most relevant facts about the user")
    context_parts.append('# Facts ending in "present" are currently valid')
    context_parts.append("# Facts with a past end date are NO LONGER VALID.")
    context_parts.append("<FACTS>")
    edges = getattr(search_results["edges"], "edges", [])
    context_parts.extend(format_edges(edges))
    context_parts.append("</FACTS>\n")

    # Entities
    context_parts.append(
        "# These are the most relevant entities (people, locations, organizations, items, and more)."
    )
    context_parts.append("<ENTITIES>")
    nodes = getattr(search_results["nodes"], "nodes", [])
    context_parts.extend(format_nodes(nodes))
    context_parts.append("</ENTITIES>")

    # Episodes (optional)
    if has_episodes:
        context_parts.append("\n# These are the most relevant episodes")
        context_parts.append("<EPISODES>")
        episodes = getattr(search_results["episodes"], "episodes", [])
        context_parts.extend(format_episodes(episodes))
        context_parts.append("</EPISODES>")

    # --- Document graph results (optional) ---
    if has_doc_results:
        has_doc_episodes = search_results.get("doc_episodes") is not None

        context_parts.append("\n")
        context_parts.append(
            "The following FACTS and ENTITIES are from shared reference documents.\n"
        )

        context_parts.append("# Reference document facts")
        context_parts.append("<DOCUMENT_FACTS>")
        doc_edges = getattr(search_results["doc_edges"], "edges", [])
        context_parts.extend(format_edges(doc_edges))
        context_parts.append("</DOCUMENT_FACTS>\n")

        context_parts.append("# Reference document entities")
        context_parts.append("<DOCUMENT_ENTITIES>")
        doc_nodes = getattr(search_results["doc_nodes"], "nodes", [])
        context_parts.extend(format_nodes(doc_nodes))
        context_parts.append("</DOCUMENT_ENTITIES>")

        if has_doc_episodes:
            context_parts.append("\n# Reference document episodes")
            context_parts.append("<DOCUMENT_EPISODES>")
            doc_episodes = getattr(search_results["doc_episodes"], "episodes", [])
            context_parts.extend(format_episodes(doc_episodes))
            context_parts.append("</DOCUMENT_EPISODES>")

    return "\n".join(context_parts)


def construct_agent_context(
    loop_result: AgentLoopResult, context_block: str | None = None
) -> str:
    """
    Assemble the context that context completeness is graded on in tool mode.

    Everything the agent retrieved counts: the pre-injected context block (when
    deterministic retrieval also ran) plus the full output of every successful
    tool call. Grading the union of tool outputs — rather than just what ended up
    in the answer — keeps completeness a measure of retrieval, not of generation.

    Only Zep's output goes in. The model's own query strings are deliberately
    left out: a query like "did the user cancel the Boston trip" would otherwise
    put "Boston" in the graded context and let the judge credit retrieval for a
    detail the model guessed. Failed and refused calls are excluded for the same
    reason — their output is harness error text, not retrieved context. Both are
    still recorded in tool_trace.

    Args:
        loop_result: Result of the agent loop, including all tool call records
        context_block: Deterministic context block, if one was injected

    Returns:
        Formatted context string for the completeness judge
    """
    parts = []

    if context_block:
        parts.append("# Context block retrieved before the agent ran")
        parts.append("<CONTEXT_BLOCK>")
        parts.append(context_block)
        parts.append("</CONTEXT_BLOCK>\n")

    parts.append("# Everything the agent retrieved from Zep via its own tool calls")
    parts.append("<TOOL_RESULTS>")

    retrieved = loop_result.retrieved_calls
    if not retrieved:
        parts.append("The agent retrieved nothing via tool calls.")
    for i, call in enumerate(retrieved, start=1):
        parts.append(f"## Result {i} of {len(retrieved)} (from {call.name})")
        parts.append(call.output)
        parts.append("")

    parts.append("</TOOL_RESULTS>")

    return "\n".join(parts)


# ============================================================================
# Step 3: Generate AI Response
# ============================================================================


async def generate_ai_response(
    llm_client: AsyncOpenAI, context: str, question: str
) -> Tuple[str, int, int]:
    """
    Generate an answer to a question using the provided Zep context.

    Args:
        llm_client: AsyncOpenAI client instance (pointed at Gemini)
        context: Retrieved context from Zep graph search
        question: Question to answer

    Returns:
        Tuple of (AI-generated answer string, prompt tokens, completion tokens)
    """
    system_prompt = get_response_system_prompt(context)

    response = await retry_with_backoff(
        llm_client.chat.completions.create,
        model=LLM_RESPONSE_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
        temperature=0.0,
        description=f"generate response for '{question[:60]}'",
    )

    # content is None when the provider returns a choice with no text (safety
    # block, truncated output) — treat it as an empty answer rather than crashing
    # the run partway through.
    message = response.choices[0].message if response.choices else None
    answer = (getattr(message, "content", "") or "").strip()
    prompt_tokens = response.usage.prompt_tokens if response.usage else 0
    completion_tokens = response.usage.completion_tokens if response.usage else 0

    return answer, prompt_tokens, completion_tokens


# ============================================================================
# Step 4: Grade AI Response
# ============================================================================


async def grade_ai_response(
    llm_client: AsyncOpenAI, question: str, golden_answer: str, ai_response: str
) -> Tuple[bool, str]:
    """
    Grade an AI response against golden answer using an LLM judge.

    Args:
        llm_client: AsyncOpenAI client instance (pointed at Gemini)
        question: The original question
        golden_answer: The expected correct answer
        ai_response: The AI-generated response to evaluate

    Returns:
        Tuple of (is_correct: bool, reasoning: str)
    """
    system_prompt, grading_prompt = get_accuracy_judge_prompts(
        question, golden_answer, ai_response
    )

    async def _parse():
        response = await llm_client.beta.chat.completions.parse(
            model=LLM_JUDGE_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": grading_prompt},
            ],
            response_format=Grade,
            temperature=0.0,
        )
        result = response.choices[0].message.parsed
        if result is None:
            raise ValueError(f"LLM judge returned unparseable response for '{question[:60]}'")
        return result

    result = await retry_with_backoff(
        _parse,
        description=f"grade response for '{question[:60]}'",
    )
    return result.correct, result.reasoning


# ============================================================================
# Step 4b: Evaluate Context Completeness (PRIMARY METRIC)
# ============================================================================


async def evaluate_context_completeness(
    llm_client: AsyncOpenAI, question: str, golden_answer: str, context: str
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
    system_prompt, completeness_prompt = get_completeness_judge_prompts(
        question, golden_answer, context
    )

    async def _parse():
        response = await llm_client.beta.chat.completions.parse(
            model=LLM_JUDGE_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": completeness_prompt},
            ],
            response_format=CompletenessGrade,
            temperature=0.0,
        )
        result = response.choices[0].message.parsed
        if result is None:
            raise ValueError(f"LLM judge returned unparseable response for '{question[:60]}'")
        return result

    result = await retry_with_backoff(
        _parse,
        description=f"evaluate completeness for '{question[:60]}'",
    )
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


async def _timed(coro) -> Tuple[Any, float]:
    """Await a coroutine and return (result, duration_ms)."""
    start = time()
    result = await coro
    return result, (time() - start) * 1000


async def process_single_query(
    zep_client: AsyncZep,
    llm_client: AsyncOpenAI,
    user_id: str,
    query: str,
    golden_answer: str,
    doc_graph_id: str | None = None,
    user_summary: str | None = None,
    use_context_block: bool = True,
    tool_registry: ToolRegistry | None = None,
    max_tool_iterations: int = MAX_TOOL_ITERATIONS,
    max_tool_calls: int = MAX_TOOL_CALLS,
    require_tool_call: bool = REQUIRE_TOOL_CALL,
) -> Dict[str, Any]:
    """
    Process a single query through the complete pipeline.

    Without tools:
        Search → Context Completeness (PRIMARY) ∥ Generate Response → Grade Answer (SECONDARY)
    With tools:
        [optional Search] → Agent retrieves via tools, then answers
        → Context Completeness (PRIMARY, over all tool outputs) ∥ Grade Answer (SECONDARY)

    Args:
        zep_client: AsyncZep client instance
        llm_client: AsyncOpenAI client instance
        user_id: User ID for graph search
        query: Question to answer
        golden_answer: Expected answer for evaluation
        doc_graph_id: Optional standalone document graph to also search
        user_summary: Optional user summary from the user node
        use_context_block: Run deterministic retrieval and inject a context block
        tool_registry: Active tools; None or empty disables agentic retrieval
        max_tool_iterations: Max LLM turns that may request tools
        max_tool_calls: Max individual tool calls across all iterations
        require_tool_call: Force a tool call on the agent's first turn

    Returns:
        Dictionary containing all results for this query
    """
    use_tools = bool(tool_registry)
    start_time = time()

    # Step 1: Deterministic retrieval (user graph + optionally document graph)
    context_block: str | None = None
    search_duration_ms = 0.0
    if use_context_block:
        include_episodes = USER_EPISODES_LIMIT > 0 or DOC_EPISODES_LIMIT > 0
        search_results, search_duration_ms = await _timed(
            perform_graph_search(
                zep_client,
                user_id,
                query,
                include_episodes=include_episodes,
                doc_graph_id=doc_graph_id,
            )
        )
        context_block = construct_context_block(search_results, user_summary=user_summary)

    loop_result: AgentLoopResult | None = None

    if use_tools:
        # Step 2: The agent retrieves what it needs via tools, then answers.
        loop_result, response_duration_ms = await _timed(
            run_tool_agent(
                llm_client,
                LLM_RESPONSE_MODEL,
                get_tool_agent_system_prompt(
                    context_block,
                    max_iterations=max_tool_iterations,
                    max_tool_calls=max_tool_calls,
                ),
                query,
                tool_registry,
                ToolContext(
                    zep_client=zep_client,
                    user_id=user_id,
                    doc_graph_id=doc_graph_id,
                    user_summary=user_summary,
                ),
                max_iterations=max_tool_iterations,
                max_tool_calls=max_tool_calls,
                require_tool_call=require_tool_call,
                has_context_block=context_block is not None,
            )
        )
        ai_answer = loop_result.answer
        prompt_tokens = loop_result.prompt_tokens
        completion_tokens = loop_result.completion_tokens

        # Completeness is graded on everything the agent retrieved, not just on
        # what it used, so it can only run once the agent is done.
        context = construct_agent_context(loop_result, context_block)

        # Steps 3 & 4: Grade context and answer in parallel
        (completeness, completeness_duration_ms), (grade, grading_duration_ms) = (
            await asyncio.gather(
                _timed(
                    evaluate_context_completeness(
                        llm_client, query, golden_answer, context
                    )
                ),
                _timed(grade_ai_response(llm_client, query, golden_answer, ai_answer)),
            )
        )
    else:
        context = context_block or ""

        # Completeness doesn't depend on the answer, so it overlaps generation.
        (completeness, completeness_duration_ms), (response, response_duration_ms) = (
            await asyncio.gather(
                _timed(
                    evaluate_context_completeness(
                        llm_client, query, golden_answer, context
                    )
                ),
                _timed(generate_ai_response(llm_client, context, query)),
            )
        )
        ai_answer, prompt_tokens, completion_tokens = response

        # Step 4: Grade Response (SECONDARY METRIC) - must wait for AI answer
        grade, grading_duration_ms = await _timed(
            grade_ai_response(llm_client, query, golden_answer, ai_answer)
        )

    completeness_grade, completeness_reasoning, missing_elements, present_elements = (
        completeness
    )
    answer_grade, answer_reasoning = grade

    total_duration_ms = (time() - start_time) * 1000
    # What a real user would wait for: retrieval + answer, excluding the judges.
    answer_latency_ms = search_duration_ms + response_duration_ms

    # Print result with PRIMARY metric first
    completeness_prefix = {
        "COMPLETE": "[✓]",
        "PARTIAL": "[~]",
        "INSUFFICIENT": "[✗]",
    }.get(completeness_grade, "[ ]")

    answer_status = "[✓] CORRECT" if answer_grade else "[✗] WRONG"

    print(f"Question: {query}")
    print(f"  Gold: {golden_answer}")
    if loop_result:
        calls = ", ".join(
            f"{name}×{count}" for name, count in loop_result.call_counts().items()
        )
        print(
            f"  [T] Tools: {len(loop_result.executed_calls)} calls in "
            f"{loop_result.iterations} round(s) [{calls or 'none'}] — "
            f"{loop_result.tool_wall_ms:.0f}ms in tools, "
            f"{loop_result.llm_ms:.0f}ms in LLM"
        )
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
        "tool_duration_ms": loop_result.tool_wall_ms if loop_result else 0.0,
        "tool_call_duration_ms": loop_result.tool_call_ms if loop_result else 0.0,
        "answer_llm_duration_ms": (
            loop_result.llm_ms if loop_result else response_duration_ms
        ),
        "grading_duration_ms": grading_duration_ms,
        "answer_latency_ms": answer_latency_ms,
        "total_duration_ms": total_duration_ms,
        # Token usage. In tool mode the prompt count is the SUM over every turn,
        # and each turn re-sends the history — per-turn counts are kept separately
        # so the total stays decomposable.
        "response_prompt_tokens": prompt_tokens,
        "response_completion_tokens": completion_tokens,
        "response_prompt_tokens_per_turn": (
            loop_result.turn_prompt_tokens if loop_result else [prompt_tokens]
        ),
        "llm_turns": loop_result.llm_turns if loop_result else 1,
        # Tool usage
        "tool_calls": len(loop_result.calls) if loop_result else 0,
        "tool_calls_executed": len(loop_result.executed_calls) if loop_result else 0,
        "tool_calls_retrieved": len(loop_result.retrieved_calls) if loop_result else 0,
        "tool_iterations": loop_result.iterations if loop_result else 0,
        "tool_errors": loop_result.count(tool_agent.ERROR) if loop_result else 0,
        "tool_invalid_calls": loop_result.count(tool_agent.INVALID) if loop_result else 0,
        "tool_refused_calls": loop_result.count(tool_agent.REFUSED) if loop_result else 0,
        "tool_choice_downgrades": (
            loop_result.tool_choice_downgrades if loop_result else 0
        ),
        "require_tool_call_unenforced": (
            loop_result.require_tool_call_unenforced if loop_result else False
        ),
        "forced_answer_failed": (
            loop_result.forced_answer_failed if loop_result else False
        ),
        "hit_tool_call_cap": loop_result.hit_call_cap if loop_result else False,
        "hit_tool_iteration_cap": (
            loop_result.hit_iteration_cap if loop_result else False
        ),
        "answer_empty": loop_result.answer_empty if loop_result else not ai_answer,
        "tool_trace": [call.summary() for call in loop_result.calls]
        if loop_result
        else [],
    }


# ============================================================================
# Step 6: Run Complete Evaluation Pipeline
# ============================================================================


async def evaluate_all_questions(
    zep_client: AsyncZep,
    llm_client: AsyncOpenAI,
    manifest: Dict[str, Any],
    test_cases_by_user: Dict[str, List[Dict[str, Any]]],
    doc_graph_id: str | None = None,
    concurrency: int = 15,
    use_context_block: bool = True,
    tool_registry: ToolRegistry | None = None,
    max_tool_iterations: int = MAX_TOOL_ITERATIONS,
    max_tool_calls: int = MAX_TOOL_CALLS,
    require_tool_call: bool = REQUIRE_TOOL_CALL,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Run the complete evaluation pipeline for all users and their test cases.

    Args:
        doc_graph_id: If provided, also search this standalone document graph
        concurrency: Max concurrent test case evaluations (semaphore limit)
        use_context_block: Run deterministic retrieval and inject a context block
        tool_registry: Active tools; None or empty disables agentic retrieval
        max_tool_iterations: Max LLM turns that may request tools
        max_tool_calls: Max individual tool calls across all iterations
        require_tool_call: Force a tool call on the agent's first turn

    Returns:
        Dictionary mapping user_id to list of evaluation results
    """
    all_results = {}
    semaphore = asyncio.Semaphore(concurrency)

    # Map base user IDs to actual Zep user IDs
    user_mapping = {}
    for user_data in manifest["users"]:
        base_id = user_data["base_user_id"]
        zep_id = user_data["zep_user_id"]
        user_mapping[base_id] = zep_id

    # Warm the document graph by running a simple search (no warm method for
    # standalone graphs, so a lightweight search primes the cache)
    if doc_graph_id:
        print(f"Warming document graph {doc_graph_id}...")
        # No reranker: this primes the cache with a single result, so ranking is
        # irrelevant — and passing the context block's reranker would drag its
        # configuration into runs that disabled the context block entirely.
        await zep_client.graph.search(
            graph_id=doc_graph_id, query=".", scope="edges", limit=1,
        )
        print(f"✓ Document graph warmed\n")

    # Process each user
    for base_user_id, test_cases in test_cases_by_user.items():
        if base_user_id not in user_mapping:
            print(f"Warning: User {base_user_id} not found in manifest, skipping")
            continue

        zep_user_id = user_mapping[base_user_id]
        print(f"\n{'='*80}")
        print(f"Evaluating user: {base_user_id} → {zep_user_id}")
        print(f"Test cases: {len(test_cases)}")
        print(f"Concurrency: {concurrency}")
        if doc_graph_id:
            print(f"Document graph: {doc_graph_id}")
        print(f"Retrieval: {describe_retrieval_mode(use_context_block, tool_registry)}")
        print(f"{'='*80}\n")

        # Warm the user's graph cache for low-latency search
        print(f"Warming graph cache for user {zep_user_id}...")
        await zep_client.user.warm(user_id=zep_user_id)
        print(f"✓ Graph cache warmed for {zep_user_id}")

        # Fetch user summary from the user node
        user_summary = None
        try:
            user_node_response = await zep_client.user.get_node(user_id=zep_user_id)
            if user_node_response.node:
                user_summary = getattr(user_node_response.node, "summary", None)
            if user_summary:
                print(f"✓ User summary retrieved")
            else:
                print(f"  No user summary available")
        except Exception as e:
            print(f"  Could not retrieve user summary: {e}")
        print()

        # Process all test cases concurrently, bounded by semaphore
        completed = 0
        total = len(test_cases)

        async def _run_one(test_case):
            nonlocal completed
            query = test_case["query"]

            async with semaphore:
                result = await process_single_query(
                    zep_client,
                    llm_client,
                    zep_user_id,
                    query,
                    test_case["golden_answer"],
                    doc_graph_id=doc_graph_id,
                    user_summary=user_summary,
                    use_context_block=use_context_block,
                    tool_registry=tool_registry,
                    max_tool_iterations=max_tool_iterations,
                    max_tool_calls=max_tool_calls,
                    require_tool_call=require_tool_call,
                )
            result["test_id"] = test_case.get("id")
            # Falls back to a string: a None category would break the sorted()
            # per-category breakdown as soon as one test case omits the field.
            result["category"] = test_case.get("category") or "unknown"
            if "needles" in test_case:
                result["needles"] = test_case["needles"]
            completed += 1
            if completed % 10 == 0 or completed == total:
                print(f"  Progress: {completed}/{total} test cases completed")
            return result

        user_results = await asyncio.gather(*[
            _run_one(tc) for tc in test_cases
        ])

        all_results[base_user_id] = list(user_results)

        print(f"\n✓ Completed evaluation for user {base_user_id}\n")

    return all_results


# ============================================================================
# Step 6: Save and Analyze Results
# ============================================================================


def _median_stdev(values: List[float]) -> Tuple[float, float]:
    """Return (median, stdev) for a list of numbers, safe for 0 or 1 samples."""
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return values[0], 0.0
    return statistics.median(values), statistics.stdev(values)


def unsupported_rerankers(
    use_context_block: bool, tools_enabled: bool
) -> List[Tuple[str, str]]:
    """
    Configured rerankers that the harness cannot use, for the active paths only.

    A disabled path's reranker never reaches Zep, so checking it would couple the
    two independently toggleable retrieval modes: a tools-only run would fail on
    the context block's reranker and vice versa.

    Returns:
        List of (constant name, configured value) for each unusable reranker.
    """
    configured = []
    if use_context_block:
        configured.append(("CONTEXT_BLOCK_RERANKER", CONTEXT_BLOCK_RERANKER))
    if tools_enabled:
        configured.append(("TOOL_SEARCH_RERANKER", TOOL_SEARCH_RERANKER))
    return [
        (label, reranker)
        for label, reranker in configured
        if reranker not in SUPPORTED_RERANKERS
    ]


def describe_retrieval_mode(
    use_context_block: bool, tool_registry: Optional[ToolRegistry]
) -> str:
    """One-line description of how context is being retrieved."""
    parts = []
    if use_context_block:
        parts.append("context block")
    if tool_registry:
        parts.append(f"tools ({', '.join(tool_registry.names())})")
    return " + ".join(parts) if parts else "none"


def _compute_tool_statistics(
    items: List[Dict[str, Any]], enabled: bool = False
) -> Dict[str, Any]:
    """
    Aggregate tool usage and per-tool latency across all test cases.

    `enabled` records whether tools were configured for the run, which is what
    the summary keys off — a run where tools were available but never called is a
    finding, not a reason to hide the tool section.
    """
    calls_per_test = [r.get("tool_calls_executed", 0) for r in items]
    iterations_per_test = [r.get("tool_iterations", 0) for r in items]

    per_tool: Dict[str, Dict[str, Any]] = {}
    for result in items:
        for call in result.get("tool_trace", []):
            if not call.get("executed", True):
                continue
            stats = per_tool.setdefault(
                call["name"], {"calls": 0, "errors": 0, "_latencies": []}
            )
            stats["calls"] += 1
            stats["_latencies"].append(call.get("duration_ms", 0.0))
            if not call.get("ok", True):
                stats["errors"] += 1

    for name, stats in per_tool.items():
        latencies = stats.pop("_latencies")
        median, stdev = _median_stdev(latencies)
        stats["latency_median_ms"] = median
        stats["latency_stdev_ms"] = stdev
        stats["latency_total_ms"] = sum(latencies)

    calls_median, calls_stdev = _median_stdev(calls_per_test)
    iterations_median, iterations_stdev = _median_stdev(iterations_per_test)

    return {
        "enabled": enabled,
        "total_calls": sum(calls_per_test),
        "calls_median": calls_median,
        "calls_stdev": calls_stdev,
        "iterations_median": iterations_median,
        "iterations_stdev": iterations_stdev,
        # Calls that ran and raised
        "errors": sum(r.get("tool_errors", 0) for r in items),
        # Calls never dispatched: unknown tool name or unparseable arguments
        "invalid_calls": sum(r.get("tool_invalid_calls", 0) for r in items),
        # Calls never dispatched because the call budget was spent
        "refused_calls": sum(r.get("tool_refused_calls", 0) for r in items),
        # Turns where the provider rejected tool_choice and it was relaxed
        "tool_choice_downgrades": sum(
            r.get("tool_choice_downgrades", 0) for r in items
        ),
        # Tests where require_tool_call was asked for but not applied
        "tests_require_tool_call_unenforced": sum(
            1 for r in items if r.get("require_tool_call_unenforced")
        ),
        # Only meaningful when tools were offered: otherwise every test would
        # trivially count as "no tool calls".
        "tests_with_no_calls": (
            sum(1 for c in calls_per_test if c == 0) if enabled else 0
        ),
        "tests_hitting_call_cap": sum(
            1 for r in items if r.get("hit_tool_call_cap")
        ),
        "tests_hitting_iteration_cap": sum(
            1 for r in items if r.get("hit_tool_iteration_cap")
        ),
        # The agent never produced a clean answer — it kept asking for tools
        "tests_forced_answer_failed": sum(
            1 for r in items if r.get("forced_answer_failed")
        ),
        "per_tool": dict(sorted(per_tool.items())),
    }


def _compute_scores(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute completeness and accuracy scores for a list of result items."""
    total = len(items)
    if total == 0:
        return {
            "total_tests": 0,
            "completeness": {
                "complete": 0, "partial": 0, "insufficient": 0,
                "complete_rate": 0, "partial_rate": 0, "insufficient_rate": 0,
            },
            "accuracy": {"correct": 0, "incorrect": 0, "accuracy_rate": 0},
        }
    complete = sum(1 for r in items if r["completeness_grade"] == "COMPLETE")
    partial = sum(1 for r in items if r["completeness_grade"] == "PARTIAL")
    insufficient = sum(1 for r in items if r["completeness_grade"] == "INSUFFICIENT")
    correct = sum(1 for r in items if r["answer_grade"])
    return {
        "total_tests": total,
        "completeness": {
            "complete": complete, "partial": partial, "insufficient": insufficient,
            "complete_rate": complete / total * 100,
            "partial_rate": partial / total * 100,
            "insufficient_rate": insufficient / total * 100,
        },
        "accuracy": {
            "correct": correct,
            "incorrect": total - correct,
            "accuracy_rate": correct / total * 100,
        },
    }


def calculate_aggregate_statistics(
    results: Dict[str, List[Dict[str, Any]]],
    tools_enabled: bool = False,
) -> Dict[str, Any]:
    """
    Calculate aggregate statistics across all users and per-user statistics.

    Args:
        results: Per-user evaluation results
        tools_enabled: Whether the run gave the model retrieval tools. Recorded
            so the summary reports tool metrics even when zero calls were made.

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

    # Timing statistics — end-to-end, plus each stage in isolation.
    # answer_latency = search + response, i.e. what a user would wait for.
    # response splits into tool time and answer-LLM time when tools are on.
    def _timing(key: str) -> Tuple[float, float]:
        return _median_stdev([r.get(key, 0.0) for r in all_user_results])

    median_total, stdev_total = _timing("total_duration_ms")
    median_answer_latency, stdev_answer_latency = _timing("answer_latency_ms")
    median_search, stdev_search = _timing("search_duration_ms")
    median_response, stdev_response = _timing("response_duration_ms")
    median_tool, stdev_tool = _timing("tool_duration_ms")
    median_tool_calls_time, stdev_tool_calls_time = _timing("tool_call_duration_ms")
    median_answer_llm, stdev_answer_llm = _timing("answer_llm_duration_ms")
    median_completeness, stdev_completeness = _timing("completeness_duration_ms")
    median_grading, stdev_grading = _timing("grading_duration_ms")

    # Token statistics
    prompt_tokens_list = [r["response_prompt_tokens"] for r in all_user_results]
    completion_tokens_list = [
        r.get("response_completion_tokens", 0) for r in all_user_results
    ]
    median_prompt_tokens, stdev_prompt_tokens = _median_stdev(prompt_tokens_list)
    median_completion_tokens, stdev_completion_tokens = _median_stdev(
        completion_tokens_list
    )
    median_llm_turns, stdev_llm_turns = _median_stdev(
        [r.get("llm_turns", 1) for r in all_user_results]
    )

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
            # Whole pipeline including the LLM judges (not user-facing latency)
            "total_median_ms": median_total,
            "total_stdev_ms": stdev_total,
            # User-facing latency: deterministic search + answer generation
            "answer_latency_median_ms": median_answer_latency,
            "answer_latency_stdev_ms": stdev_answer_latency,
            # Deterministic context block retrieval (0 when disabled)
            "search_median_ms": median_search,
            "search_stdev_ms": stdev_search,
            # Answer generation as a whole (tool loop included when tools are on)
            "response_median_ms": median_response,
            "response_stdev_ms": stdev_response,
            # Tool execution only — wall clock, so parallel calls count once
            "tool_median_ms": median_tool,
            "tool_stdev_ms": stdev_tool,
            # Sum of individual tool call durations (exceeds wall clock when parallel)
            "tool_calls_summed_median_ms": median_tool_calls_time,
            "tool_calls_summed_stdev_ms": stdev_tool_calls_time,
            # Answer generation minus tool execution: the "other" half of response
            "answer_llm_median_ms": median_answer_llm,
            "answer_llm_stdev_ms": stdev_answer_llm,
            # Judges (evaluation overhead, not part of user-facing latency)
            "grading_median_ms": median_grading,
            "grading_stdev_ms": stdev_grading,
            "completeness_median_ms": median_completeness,
            "completeness_stdev_ms": stdev_completeness,
        },
        "tokens": {
            "prompt_median": median_prompt_tokens,
            "prompt_stdev": stdev_prompt_tokens,
            "total_prompt": sum(prompt_tokens_list),
            "completion_median": median_completion_tokens,
            "completion_stdev": stdev_completion_tokens,
            "total_completion": sum(completion_tokens_list),
            "llm_turns_median": median_llm_turns,
            "llm_turns_stdev": stdev_llm_turns,
        },
        "tools": _compute_tool_statistics(all_user_results, enabled=tools_enabled),
        "empty_answers": sum(1 for r in all_user_results if r.get("answer_empty")),
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

    # Per-category breakdown
    category_items = defaultdict(list)
    for r in all_user_results:
        cat = r.get("category", "unknown")
        category_items[cat].append(r)
    category_scores = {cat: _compute_scores(items) for cat, items in sorted(category_items.items())}

    return {
        "user_scores": user_scores,
        "aggregate_scores": aggregate_scores,
        "category_scores": category_scores,
    }


def _get_next_eval_run_number() -> int:
    """Get the next evaluation run number by checking existing run directories."""
    os.makedirs("runs/evaluations", exist_ok=True)
    existing = glob.glob("runs/evaluations/*")
    run_numbers = []
    for d in existing:
        if not os.path.isdir(d):
            continue
        try:
            run_numbers.append(int(os.path.basename(d).split("_")[0]))
        except (IndexError, ValueError):
            continue
    return max(run_numbers) + 1 if run_numbers else 1


def save_results(
    results: Dict[str, List[Dict[str, Any]]],
    user_manifest: Dict[str, Any],
    user_run_dir: str,
    doc_manifest: Optional[Dict[str, Any]] = None,
    doc_run_dir: Optional[str] = None,
    retrieval_configuration: Optional[Dict[str, Any]] = None,
):
    """
    Save evaluation results to runs/evaluations/{number}_{timestamp}/.
    References the parent user and document ingestion runs.
    """
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    run_number = _get_next_eval_run_number()
    eval_run_dir = f"runs/evaluations/{run_number}_{timestamp}"
    os.makedirs(eval_run_dir, exist_ok=True)

    results_file = os.path.join(eval_run_dir, "results.json")

    # Snapshot the evaluation config used for this run
    snapshot_dir = os.path.join(eval_run_dir, "evaluation_config_snapshot")
    shutil.copytree(
        "config/evaluation_config", snapshot_dir,
        ignore=shutil.ignore_patterns("__pycache__"),
    )

    # Calculate statistics
    stats = calculate_aggregate_statistics(
        results, tools_enabled=bool((retrieval_configuration or {}).get("use_tools"))
    )

    # Build parent run references
    parent_runs = {
        "user_run": {
            "run_number": user_manifest.get("run_number"),
            "run_dir": user_run_dir,
        },
    }
    if doc_manifest:
        parent_runs["document_run"] = {
            "run_number": doc_manifest.get("run_number"),
            "run_dir": doc_run_dir,
            "graph_id": doc_manifest.get("graph_id"),
        }

    # Prepare output structure
    output_data = {
        "evaluation_timestamp": timestamp,
        "run_number": run_number,
        "parent_runs": parent_runs,
        "retrieval_configuration": retrieval_configuration or {},
        # Context block search settings. Applied only when the context block ran
        # — see retrieval_configuration.tool_search for what bounded tool searches.
        "search_configuration": {
            "applied": bool((retrieval_configuration or {}).get("use_context_block", True)),
            "user_facts_limit": USER_FACTS_LIMIT,
            "user_entities_limit": USER_ENTITIES_LIMIT,
            "user_episodes_limit": USER_EPISODES_LIMIT,
            "doc_facts_limit": DOC_FACTS_LIMIT,
            "doc_entities_limit": DOC_ENTITIES_LIMIT,
            "doc_episodes_limit": DOC_EPISODES_LIMIT,
            "reranker": CONTEXT_BLOCK_RERANKER,
        },
        "model_configuration": {
            "response_model": LLM_RESPONSE_MODEL,
            "judge_model": LLM_JUDGE_MODEL,
        },
        "aggregate_scores": stats["aggregate_scores"],
        "category_scores": stats.get("category_scores", {}),
        "user_scores": stats["user_scores"],
        "detailed_results": results,
    }

    with open(results_file, "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"\n{'='*80}")
    print(f"Evaluation run #{run_number} saved to: {eval_run_dir}/")
    print(f"{'='*80}")

    return results_file, stats


def _category_label(slug: str) -> str:
    """Convert a snake_case category slug to a human-readable label."""
    return slug.replace("_", " ").title()


def print_summary(stats: Dict[str, Any]):
    """
    Print summary statistics for the evaluation.
    """
    aggregate = stats["aggregate_scores"]
    user_scores = stats["user_scores"]
    category_scores = stats.get("category_scores", {})

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

    # Per-Category Breakdown
    if category_scores:
        print(f"\n{'='*80}")
        print("PER-CATEGORY SCORES")
        print(f"{'='*80}\n")
        for cat, scores in category_scores.items():
            label = _category_label(cat)
            n = scores["total_tests"]
            c = scores["completeness"]
            a = scores["accuracy"]
            print(f"{label} ({n} tests):")
            print(
                f"  Completeness: COMPLETE={c['complete_rate']:.1f}%, "
                f"PARTIAL={c['partial_rate']:.1f}%, "
                f"INSUFFICIENT={c['insufficient_rate']:.1f}%"
            )
            print(
                f"  Accuracy:     {a['accuracy_rate']:.1f}% "
                f"({a['correct']}/{n} correct)"
            )
            print()

    # Timing
    timing = aggregate["timing"]
    tools = aggregate.get("tools", {})
    # Keyed off whether tools were configured, not off whether any were called:
    # "tools were available and the agent never used them" is the finding that
    # most needs to be visible.
    tools_used = tools.get("enabled", tools.get("total_calls", 0) > 0)
    tokens = aggregate.get("tokens", {})

    # Reads are defensive so a results.json written by an earlier version of the
    # harness (fewer timing/token keys) can still be summarized.
    def _t(label: str, key: str, indent: int = 2) -> None:
        if f"{key}_median_ms" not in timing:
            return
        pad = " " * indent
        print(
            f"{pad}{label:<{34 - indent}}"
            f"{timing[f'{key}_median_ms']:.0f} ± {timing.get(f'{key}_stdev_ms', 0):.0f}ms"
        )

    print(f"\nTiming (median ± stdev per query):")
    _t("Answer latency (user-facing):", "answer_latency")
    _t("Context block search:", "search", indent=4)
    _t("Answer generation:", "response", indent=4)
    if tools_used:
        _t("Tool execution (wall):", "tool", indent=6)
        _t("Tool calls (summed):", "tool_calls_summed", indent=6)
        _t("Answer LLM (non-tool):", "answer_llm", indent=6)
    print()
    _t("Completeness eval (judge):", "completeness")
    _t("Accuracy eval (judge):", "grading")
    _t("Total incl. judges:", "total")

    # Tool Usage
    if tools_used:
        print(f"\nTool Usage:")
        print(
            f"  Calls per query:          {tools['calls_median']:.1f} ± {tools['calls_stdev']:.1f}"
            f"  (total {tools['total_calls']})"
        )
        print(
            f"  Rounds per query:         {tools['iterations_median']:.1f} ± {tools['iterations_stdev']:.1f}"
        )
        no_calls = tools["tests_with_no_calls"]
        print(
            f"  Tests with no tool calls: {no_calls}/{total_tests}"
            + ("   ← agent never retrieved" if no_calls else "")
        )
        print(
            f"  Hit call cap:             {tools['tests_hitting_call_cap']}/{total_tests}"
            f"   |  Hit round cap: {tools['tests_hitting_iteration_cap']}/{total_tests}"
        )
        if tools["errors"] or tools["invalid_calls"] or tools["refused_calls"]:
            print(
                f"  Failed calls: {tools['errors']}  |  Invalid (bad name/args): "
                f"{tools['invalid_calls']}  |  Refused (over budget): {tools['refused_calls']}"
            )
        if tools.get("tests_forced_answer_failed"):
            print(
                f"  ⚠ Never answered cleanly: {tools['tests_forced_answer_failed']}"
                f"/{total_tests} kept requesting tools through every forced-answer "
                f"attempt (any text they produced is a preamble, not an answer)"
            )
        if tools["tool_choice_downgrades"]:
            print(
                f"  ⚠ tool_choice downgraded on {tools['tool_choice_downgrades']} turn(s) "
                f"— the provider rejected the requested tool_choice"
            )
        if tools.get("tests_require_tool_call_unenforced"):
            print(
                f"  ⚠ require_tool_call not applied on "
                f"{tools['tests_require_tool_call_unenforced']}/{total_tests} tests "
                f"— the provider either rejected it or answered without retrieving"
            )
        if tools["per_tool"]:
            print(f"\n  Per tool:")
            for name, stats in tools["per_tool"].items():
                print(
                    f"    {name:<24} {stats['calls']:4d} calls, "
                    f"{stats['latency_median_ms']:.0f} ± {stats['latency_stdev_ms']:.0f}ms"
                    + (f", {stats['errors']} failed" if stats["errors"] else "")
                )

    # Empty answers can happen in either mode, so report them unconditionally
    if aggregate.get("empty_answers"):
        print(
            f"\n⚠ Empty answers: {aggregate['empty_answers']}/{total_tests} "
            f"(graded WRONG — the model returned no text)"
        )

    # Token Usage
    print(f"\nToken Usage:")
    print(
        f"  Prompt tokens per query: {tokens.get('prompt_median', 0):.0f} ± {tokens.get('prompt_stdev', 0):.0f}"
        + ("  (summed across turns)" if tools_used else "")
    )
    print(f"  Total prompt tokens:     {tokens.get('total_prompt', 0)}")
    if "completion_median" in tokens:
        print(
            f"  Completion tokens per query: {tokens['completion_median']:.0f} ± {tokens.get('completion_stdev', 0):.0f}"
        )
    if tools_used and "llm_turns_median" in tokens:
        print(
            f"  LLM turns per query:     {tokens['llm_turns_median']:.1f} ± {tokens.get('llm_turns_stdev', 0):.1f}"
        )

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


def parse_args():
    parser = argparse.ArgumentParser(
        description="Zep Eval Harness — Evaluation Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  uv run zep_evaluate.py                                    # Evaluate latest user run (no document graph)
  uv run zep_evaluate.py --user-run 3                       # Evaluate user run #3
  uv run zep_evaluate.py --doc-run 2                        # Latest user run + document run #2
  uv run zep_evaluate.py --user-run 3 --doc-run 2           # Specific user run + document run

Retrieval modes:
  uv run zep_evaluate.py                                    # Context block only (deterministic search)
  uv run zep_evaluate.py --tools --no-context-block         # Agent retrieves everything via tools
  uv run zep_evaluate.py --tools                            # Context block injected, tools available too
  uv run zep_evaluate.py --tools --max-tool-calls 4 --max-tool-iterations 2
""",
    )
    parser.add_argument(
        "--user-run",
        type=int,
        default=None,
        help="User ingestion run number to evaluate (default: latest)",
    )
    parser.add_argument(
        "--doc-run",
        type=int,
        default=None,
        help="Document ingestion run number to include for document graph search (optional)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=15,
        help="Max concurrent test case evaluations (default: 15)",
    )
    parser.add_argument(
        "--tools",
        action=argparse.BooleanOptionalAction,
        default=USE_TOOLS,
        help=(
            "Give the response model the retrieval tools from "
            "config/evaluation_config/tools.py and let it retrieve its own "
            "context before answering"
        ),
    )
    parser.add_argument(
        "--context-block",
        action=argparse.BooleanOptionalAction,
        default=USE_CONTEXT_BLOCK,
        help=(
            "Run deterministic graph search up front and inject the context "
            "block into the prompt. Can be combined with --tools"
        ),
    )
    parser.add_argument(
        "--max-tool-iterations",
        type=int,
        default=MAX_TOOL_ITERATIONS,
        help=(
            "Max LLM turns that may request tools; tools called in parallel in one "
            f"turn count as one iteration (default: {MAX_TOOL_ITERATIONS})"
        ),
    )
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=MAX_TOOL_CALLS,
        help=f"Max individual tool calls per test case (default: {MAX_TOOL_CALLS})",
    )
    parser.add_argument(
        "--require-tool-call",
        action=argparse.BooleanOptionalAction,
        default=REQUIRE_TOOL_CALL,
        help=(
            "Force the agent to call at least one tool on its first turn"
        ),
    )
    return parser.parse_args()


async def main():
    load_dotenv()
    args = parse_args()

    # Validate environment variables
    zep_api_key = os.getenv("ZEP_API_KEY")
    google_api_key = os.getenv("GOOGLE_API_KEY")

    if not zep_api_key:
        print("Error: Missing ZEP_API_KEY environment variable")
        exit(1)

    if not google_api_key:
        print("Error: Missing GOOGLE_API_KEY environment variable")
        exit(1)

    # Initialize clients
    zep_client = AsyncZep(api_key=zep_api_key)
    llm_client = AsyncOpenAI(api_key=google_api_key, base_url=GEMINI_BASE_URL)

    print("=" * 80)
    print("ZEP EVALUATION SCRIPT")
    print("=" * 80)

    try:
        # Load user run manifest (always required)
        manifest, user_run_dir = load_run_manifest(args.user_run, "users")

        # Load document run manifest if --doc-run is specified
        doc_graph_id = None
        doc_manifest = None
        doc_run_dir = None
        if args.doc_run is not None:
            doc_manifest, doc_run_dir = load_run_manifest(args.doc_run, "documents")
            doc_graph_id = doc_manifest.get("graph_id")
            if doc_graph_id:
                print(f"Document graph: {doc_graph_id}")
            else:
                print("Warning: --doc-run specified but no graph_id found in document manifest")

        # Resolve the retrieval mode: context block, tools, or both
        use_context_block = args.context_block
        tool_registry = (
            ToolRegistry.build(TOOL_SPECS, doc_graph_id=doc_graph_id)
            if args.tools
            else None
        )

        if args.tools and not tool_registry:
            message = (
                "Tools are enabled but no tools are active — every spec in "
                "config/evaluation_config/tools.py is either disabled or requires "
                "a document graph (pass --doc-run)."
            )
            if not use_context_block:
                print(f"Error: {message}")
                exit(1)
            print(f"Warning: {message} Continuing with the context block only.\n")

        # Caught here rather than as an API error on every single search.
        for label, reranker in unsupported_rerankers(
            use_context_block, bool(tool_registry)
        ):
            print(
                f"Error: {label}='{reranker}' is not supported. Choose one of: "
                f"{', '.join(SUPPORTED_RERANKERS)}. (mmr and node_distance need "
                f"extra per-search arguments — see the comment in "
                f"config/evaluation_config/constants.py.)"
            )
            exit(1)

        # Semaphore(0) never admits anyone, so the run would hang after warming
        # the graphs with no output at all.
        if args.concurrency < 1:
            print(f"Error: --concurrency must be at least 1 (got {args.concurrency}).")
            exit(1)

        # A zero/negative budget silently disables every tool, which would let a
        # run report use_tools=true while retrieving nothing at all.
        if tool_registry and (args.max_tool_calls < 1 or args.max_tool_iterations < 1):
            print(
                "Error: --max-tool-calls and --max-tool-iterations must be at least 1 "
                f"when tools are enabled (got {args.max_tool_calls} calls, "
                f"{args.max_tool_iterations} iterations). Use --no-tools to disable tools."
            )
            exit(1)

        if not use_context_block and not tool_registry:
            print(
                "Error: no retrieval configured. --no-context-block requires --tools "
                "with at least one active tool, otherwise the model gets no context."
            )
            exit(1)

        retrieval_configuration = {
            "use_context_block": use_context_block,
            # What actually happened vs what was asked for: --tools with every
            # spec inactive is a real run without tools, and both facts are
            # recorded so the artifact can't be misread either way.
            "use_tools": bool(tool_registry),
            "tools_requested": bool(args.tools),
            "max_tool_iterations": args.max_tool_iterations,
            "max_tool_calls": args.max_tool_calls,
            "require_tool_call": args.require_tool_call,
            "tools": [
                {"name": spec.name, "description": spec.description}
                for spec in (tool_registry.specs.values() if tool_registry else [])
            ],
            # What actually bounded tool searches. Zep applies max_characters to
            # scope="auto" and limit/reranker to every other scope, so both are
            # recorded — the context block's own limits are in
            # search_configuration and apply only when it ran.
            "tool_search": {
                "max_characters_auto_scope": TOOL_SEARCH_MAX_CHARACTERS,
                "default_limit_other_scopes": TOOL_SEARCH_DEFAULT_LIMIT,
                "max_limit_other_scopes": TOOL_SEARCH_MAX_LIMIT,
                "reranker_other_scopes": TOOL_SEARCH_RERANKER,
            }
            if tool_registry
            else {},
        }

        # Load test cases
        test_cases_by_user = await load_all_test_cases()

        # Run evaluation
        print(
            f"Retrieval mode: "
            f"{describe_retrieval_mode(use_context_block, tool_registry)}"
        )
        if tool_registry:
            print(
                f"Tool budget: {args.max_tool_calls} calls across "
                f"{args.max_tool_iterations} round(s)"
                + ("; first tool call forced" if args.require_tool_call else "")
            )
        print(f"Starting evaluation (concurrency={args.concurrency})...\n")
        results = await evaluate_all_questions(
            zep_client, llm_client, manifest, test_cases_by_user,
            doc_graph_id=doc_graph_id,
            concurrency=args.concurrency,
            use_context_block=use_context_block,
            tool_registry=tool_registry,
            max_tool_iterations=args.max_tool_iterations,
            max_tool_calls=args.max_tool_calls,
            require_tool_call=args.require_tool_call,
        )

        # Save results with aggregate statistics
        results_file, stats = save_results(
            results, manifest, user_run_dir,
            doc_manifest=doc_manifest, doc_run_dir=doc_run_dir,
            retrieval_configuration=retrieval_configuration,
        )

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
