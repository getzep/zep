"""Evaluation pipeline for LOCOMO benchmark using graph_id."""

import asyncio
import logging
from time import time
from typing import Any

import pandas as pd
import tiktoken
from openai import AsyncOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tqdm.asyncio import tqdm
from zep_cloud import EntityEdge, EntityNode
from zep_cloud.client import AsyncZep
from zep_cloud.core.api_error import ApiError

from common import CompletenessGrade, EvaluationResult, Grade
from config import BenchmarkConfig
from prompts import (
    CONTEXT_TEMPLATE,
    GRADER_PROMPT,
    GRADER_SYSTEM_PROMPT,
    RESPONSE_PROMPT,
    RESPONSE_SYSTEM_PROMPT,
)


class EvaluationRunner:
    """Handles evaluation for LOCOMO dataset using graph_id."""

    def __init__(
        self,
        config: BenchmarkConfig,
        zep_client: AsyncZep,
        openai_client: AsyncOpenAI,
        logger: logging.Logger,
        prefix: str = "locomo",
    ):
        self.config = config
        self.zep = zep_client
        self.openai = openai_client
        self.logger = logger
        self.prefix = prefix
        self._semaphore = asyncio.Semaphore(config.evaluation_concurrency)

        # Token counter
        try:
            self.tokenizer = tiktoken.encoding_for_model(config.models.response_model)
        except KeyError:
            self.tokenizer = tiktoken.get_encoding("cl100k_base")

    @retry(
        retry=retry_if_exception_type(ApiError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.1, min=0.1, max=10),
        reraise=True,
    )
    async def _graph_search_with_retry(
        self, query: str, graph_id: str, scope: str, reranker: str, limit: int
    ):
        """Wrapper for graph.search with retry logic for 503 errors."""
        return await self.zep.graph.search(
            query=query,
            graph_id=graph_id,
            scope=scope,
            reranker=reranker,
            limit=limit,
        )

    async def evaluate_locomo(self, df: pd.DataFrame) -> list[EvaluationResult]:
        """Evaluate LOCOMO dataset."""
        self.logger.info(f"Evaluating {self.config.locomo.num_users} graphs...")

        all_results = []
        tasks = []

        for group_idx in range(self.config.locomo.num_users):
            qa_set = df["qa"].iloc[group_idx]
            graph_id = f"{self.prefix}_experiment_graph_{group_idx}"

            for qa_idx, qa in enumerate(qa_set):
                # Skip category 5 as golds are not provided for this category
                if qa.get("category") == 5:
                    continue

                test_id = f"{self.prefix}_graph_{group_idx}_qa_{qa_idx}"
                task = self._evaluate_locomo_conversation(graph_id, test_id, qa, qa_idx)
                tasks.append(task)

        # Process with progress bar
        correct_count = 0
        with tqdm(total=len(tasks), desc="Evaluating", unit="test", position=0) as pbar:
            for coro in asyncio.as_completed(tasks):
                result = await coro
                all_results.append(result)
                if result.grade:
                    correct_count += 1

                # Update metrics in progress bar
                current_accuracy = correct_count / len(all_results)
                pbar.set_postfix(
                    {"accuracy": f"{current_accuracy:.3f}", "correct": correct_count}
                )
                pbar.update(1)

        self.logger.info(
            f"Evaluation complete. Accuracy: {correct_count / len(all_results):.3f}"
        )
        return all_results

    async def _evaluate_locomo_conversation(
        self, graph_id: str, test_id: str, qa: dict[str, Any], qa_idx: int
    ) -> EvaluationResult:
        """Evaluate a single LOCOMO test case using graph_id."""
        async with self._semaphore:
            query = qa.get("question")
            gold_answer = qa.get("answer")
            category = qa.get("category", "unknown")
            difficulty = qa.get("difficulty", "unknown")

            # Retrieval with retry logic
            start_retrieval = time()
            search_results = await asyncio.gather(
                self._graph_search_with_retry(
                    query=query,
                    graph_id=graph_id,
                    scope="nodes",
                    reranker=self.config.graph_params.node_reranker,
                    limit=self.config.graph_params.node_limit,
                ),
                self._graph_search_with_retry(
                    query=query,
                    graph_id=graph_id,
                    scope="edges",
                    reranker=self.config.graph_params.edge_reranker,
                    limit=self.config.graph_params.edge_limit,
                ),
            )
            retrieval_duration = time() - start_retrieval

            nodes = search_results[0].nodes
            edges = search_results[1].edges

            # Compose context
            context = self._compose_context(edges, nodes)
            context_tokens = self._count_tokens(context)
            context_chars = len(context)

            # Response generation and completeness evaluation in parallel
            start_response = time()
            hypothesis_task = self._generate_response(context, query)
            completeness_task = self.evaluate_context_completeness(
                query, str(gold_answer), context
            )

            hypothesis, (
                completeness_grade,
                completeness_reasoning,
                missing_elements,
                present_elements,
            ) = await asyncio.gather(hypothesis_task, completeness_task)
            response_duration = time() - start_response

            # Grading
            grade, reasoning = await self._grade_response(
                query, str(gold_answer), hypothesis
            )

            total_duration = retrieval_duration + response_duration

            return EvaluationResult(
                graph_id=graph_id,
                test_id=test_id,
                category=str(category),
                difficulty=str(difficulty),
                query=query,
                golden_answer=str(gold_answer),
                hypothesis=hypothesis,
                context=context,
                context_tokens=context_tokens,
                context_chars=context_chars,
                retrieval_duration=retrieval_duration,
                response_duration=response_duration,
                total_duration=total_duration,
                grade=grade,
                grade_reasoning=reasoning,
                completeness_grade=completeness_grade,
                completeness_reasoning=completeness_reasoning,
                missing_elements=missing_elements,
                present_elements=present_elements,
            )

    def _compose_context(self, edges: list[EntityEdge], nodes: list[EntityNode]) -> str:
        """Compose context from retrieved facts and entities."""
        facts = [f"  - {edge.fact} (event_time: {edge.valid_at})" for edge in edges]
        entities = [f"  - {node.name}: {node.summary}" for node in nodes]
        return CONTEXT_TEMPLATE.format(
            facts="\n".join(facts), entities="\n".join(entities)
        )

    async def _generate_response(self, context: str, question: str) -> str:
        """Generate response using LLM."""
        prompt = RESPONSE_PROMPT.format(context=context, question=question)

        response = await self.openai.chat.completions.create(
            model=self.config.models.response_model,
            messages=[
                {"role": "system", "content": RESPONSE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=self.config.models.response_temperature,
        )

        return response.choices[0].message.content or ""

    async def _grade_response(
        self, question: str, gold_answer: str, response: str
    ) -> tuple[bool, str]:
        """Grade response using LLM."""
        grader_prompt = GRADER_PROMPT.format(
            question=question, gold_answer=gold_answer, response=response
        )

        grader_response = await self.openai.beta.chat.completions.parse(
            model=self.config.models.grader_model,
            messages=[
                {"role": "system", "content": GRADER_SYSTEM_PROMPT},
                {"role": "user", "content": grader_prompt},
            ],
            response_format=Grade,
            temperature=self.config.models.grader_temperature,
        )

        result = grader_response.choices[0].message.parsed
        is_correct = result.is_correct.strip().lower() == "correct"
        return is_correct, result.reasoning

    def _count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        try:
            return len(self.tokenizer.encode(text))
        except Exception as e:
            self.logger.warning(f"Token counting failed: {e}")
            return 0

    async def evaluate_context_completeness(
        self, question: str, gold_answer: str, context: str
    ) -> tuple[str, str, list[str], list[str]]:
        """
        Evaluate whether the retrieved context contains adequate information to answer the question.
        This is the PRIMARY evaluation metric - assessing context quality independent of the AI's answer.

        Args:
            question: The original question
            gold_answer: The expected answer (used to determine what info is needed)
            context: Retrieved context from Zep graph search

        Returns:
            Tuple of (completeness_grade, reasoning, missing_elements, present_elements)
            where completeness_grade is one of: COMPLETE, PARTIAL, INSUFFICIENT
        """
        instructions = """You are an expert evaluator assessing whether retrieved context contains adequate information to answer a question."""

        input_text = f"""Your task is to evaluate whether the provided CONTEXT contains sufficient information to answer the QUESTION according to what the GOLDEN ANSWER requires.

IMPORTANT: You are NOT evaluating an answer. You are evaluating whether the CONTEXT itself has the necessary information.

<QUESTION>
{question}
</QUESTION>

<GOLDEN ANSWER>
{gold_answer}
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

        result = await self.openai.beta.chat.completions.parse(
            model=self.config.models.grader_model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": input_text},
            ],
            response_format=CompletenessGrade,
            temperature=self.config.models.grader_temperature,
        )

        completeness_grade = (
            result.choices[0].message.parsed.completeness.strip().upper()
        )

        return (
            completeness_grade,
            result.choices[0].message.parsed.reasoning,
            result.choices[0].message.parsed.missing_elements,
            result.choices[0].message.parsed.present_elements,
        )
