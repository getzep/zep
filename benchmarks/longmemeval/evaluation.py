#!/usr/bin/env python3
"""
Evaluation module for LongMemEval benchmark
"""

import asyncio
import json
import logging
import os
import pandas as pd
from datetime import datetime, timezone
from time import time
from typing import List, Tuple

from dotenv import load_dotenv
from openai import AsyncOpenAI
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from zep_cloud import EntityEdge, EntityNode, Episode
from zep_cloud.client import AsyncZep
import httpx

from common import (
    RESPONSE_MODEL,
    GRADER_MODEL,
    GRADING_PROMPTS,
    Grade,
    CONTEXT_TEMPLATE,
)
from summarization import SummarizationService


class EvaluationRunner:
    def __init__(
        self,
        zep_dev_environment: bool = False,
        log_level: str = "INFO",
        use_summarization: bool = False,
    ):
        load_dotenv()

        self.logger = self._setup_logging(log_level)

        # Initialize Zep client
        if zep_dev_environment:
            self.zep = AsyncZep(
                api_key=os.getenv("ZEP_API_KEY"),
                base_url="https://api.development.getzep.com/api/v2",
            )
        else:
            self.zep = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))

        self.oai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        # Initialize summarization service
        self.summarization_service = SummarizationService(self.zep, self.oai_client)
        self.use_summarization = use_summarization

    def _setup_logging(self, log_level: str) -> logging.Logger:
        """Configure logging with proper formatting"""
        logger = logging.getLogger(__name__)
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)

        logger.setLevel(getattr(logging, log_level.upper()))
        return logger

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(
            (
                httpx.HTTPStatusError,
                httpx.ConnectError,
                httpx.TimeoutException,
                httpx.RequestError,
                Exception,  # Catch any other API-related exceptions
            )
        ),
    )
    async def _search_zep_with_retry(self, **kwargs):
        """Wrapper for Zep graph search with retry logic"""
        try:
            return await self.zep.graph.search(**kwargs)
        except Exception as e:
            self.logger.warning(f"Zep search failed, will retry: {e}")
            raise

    async def lme_response(self, context: str, question: str) -> str:
        """Generate response using LLM with provided context"""
        system_prompt = """
        You are a helpful expert assistant answering questions from lme_experiment users based on the provided context.
        """

        prompt = f"""
        You have access to facts and entities from a conversation.

        # INSTRUCTIONS:
        1. Carefully analyze all provided memories
        2. Pay special attention to the timestamps to determine the answer
        3. If the question asks about a specific event or fact, look for direct evidence in the memories
        4. If the memories contain contradictory information, prioritize the most recent memory
        5. Always convert relative time references to specific dates, months, or years.
        6. Be as specific as possible when talking about people, places, and events
        7. Timestamps in memories represent the actual time the event occurred, not the time the event was mentioned in a message.
        
        Clarification:
        When interpreting memories, use the timestamp to determine when the described event happened, not when someone talked about the event.
        
        Example:
        
        Memory: (2023-03-15T16:33:00Z) I went to the vet yesterday.
        Question: What day did I go to the vet?
        Correct Answer: March 15, 2023
        Explanation:
        Even though the phrase says "yesterday," the timestamp shows the event was recorded as happening on March 15th. Therefore, the actual vet visit happened on that date, regardless of the word "yesterday" in the text.


        # APPROACH (Think step by step):
        1. First, examine all memories that contain information related to the question
        2. Examine the timestamps and content of these memories carefully
        3. Look for explicit mentions of dates, times, locations, or events that answer the question
        4. If the answer requires calculation (e.g., converting relative time references), show your work
        5. Formulate a precise, concise answer based solely on the evidence in the memories
        6. Double-check that your answer directly addresses the question asked
        7. Ensure your final answer is specific and avoids vague time references
        
        CONTEXT:
        {context}
        
        QUESTION:
        {question}
        """

        response = await self.oai_client.chat.completions.create(
            model=RESPONSE_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=1 if RESPONSE_MODEL == "o4-mini" else 0,
        )
        return response.choices[0].message.content or ""

    async def lme_grader(
        self, question: str, gold_answer: str, response: str, question_type: str
    ) -> bool:
        """Grade the response against gold standard using LLM"""
        system_prompt = """
        You are an expert grader that determines if answers to questions match a gold standard answer
        """

        # Get prompt template for question type
        prompt_template = GRADING_PROMPTS.get(question_type, GRADING_PROMPTS["default"])
        prompt = prompt_template.format(
            question=question, gold_answer=gold_answer, response=response
        )

        # Get structured response
        completion = await self.oai_client.beta.chat.completions.parse(
            model=GRADER_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            response_format=Grade,
            temperature=0,
        )

        result = completion.choices[0].message.parsed
        return result.is_correct.strip().lower() == "yes"

    def compose_search_context(
        self,
        edges: List[EntityEdge],
        nodes: List[EntityNode],
        episodes: List[Episode],
    ) -> str:
        """Compose context from Zep search results"""
        # Format facts with date ranges
        facts = []
        for edge in edges:
            start_date = edge.valid_at if edge.valid_at else "date unknown"
            end_date = edge.invalid_at if edge.invalid_at else "present"
            facts.append(f"({start_date}) {edge.fact}")

        # Format entities
        entities = [
            f"{node.name} ({', '.join(node.labels) if node.labels else ''}): {node.summary} ({node.attributes})"
            for node in nodes
        ]

        # Format episodes
        episodes_content = [episode.content for episode in episodes]

        return CONTEXT_TEMPLATE.format(
            facts="\n".join(facts),
            entities="\n".join(entities),
            messages="\n".join(episodes_content),
        )

    async def compose_search_context_with_summary(
        self,
        question: str,
        edges: List[EntityEdge],
        nodes: List[EntityNode],
        episodes: List[Episode],
    ) -> str:
        """Compose context from Zep search results with AI summarization"""
        return await self.summarization_service.compose_search_context_with_summary(
            question, edges, nodes, episodes
        )

    async def evaluate_conversation(
        self, df: pd.DataFrame, multi_session_idx: int
    ) -> Tuple[dict, int, float, float]:
        """Evaluate a single conversation using Zep context retrieval"""
        # Extract question data
        question_id = df["question_id"][multi_session_idx]
        question_type = df["question_type"][multi_session_idx]
        question = f"(date: {df['question_date'][multi_session_idx]}) {df['question'][multi_session_idx]}"
        gold_answer = df["answer"][multi_session_idx]
        user_id = f"lme_s_experiment_user_{multi_session_idx}"

        # Search Zep for relevant context
        start_retrieval = time()
        edges_results, nodes_results, episodes_results = await asyncio.gather(
            self._search_zep_with_retry(
                user_id=user_id,
                query=question,
                limit=30,
                reranker="cross_encoder",
            ),
            self._search_zep_with_retry(
                user_id=user_id,
                query=question,
                scope="nodes",
                limit=30,
            ),
            self._search_zep_with_retry(
                user_id=user_id,
                query=question,
                scope="episodes",
                limit=20,
            ),
        )
        retrieval_duration = time() - start_retrieval

        # Compose context from search results
        if self.use_summarization:
            context = await self.compose_search_context_with_summary(
                question,
                edges_results.edges or [],
                nodes_results.nodes or [],
                episodes_results.episodes or [],
            )
        else:
            context = self.compose_search_context(
                edges_results.edges or [],
                nodes_results.nodes or [],
                episodes_results.episodes or [],
            )

        # Generate response
        start = time()
        hypothesis = await self.lme_response(context, question)
        duration = time() - start

        # Grade response
        grade = await self.lme_grader(question, gold_answer, hypothesis, question_type)

        result = {
            "user_id": user_id,
            "question_id": question_id,
            "hypothesis": hypothesis,
            "gold_answer": gold_answer,
            "context": context,
            "question": question,
            "question_type": question_type,
            "duration": duration,
            "grade": grade,
        }

        return result, (1 if grade else 0), duration, retrieval_duration

    async def evaluate_conversation_baseline(
        self, df: pd.DataFrame, multi_session_idx: int
    ) -> Tuple[dict, int, float]:
        """Evaluate a single conversation with baseline (full context)"""
        # Extract question data
        question_id = df["question_id"][multi_session_idx]
        question_type = df["question_type"][multi_session_idx]
        question = f"(date: {df['question_date'][multi_session_idx]}) {df['question'][multi_session_idx]}"
        gold_answer = df["answer"][multi_session_idx]

        # Build full context from all sessions
        multi_session = df["haystack_sessions"].iloc[multi_session_idx]
        multi_session_dates = df["haystack_dates"].iloc[multi_session_idx]

        context = ""
        for session_idx, session in enumerate(multi_session):
            for msg in session:
                date = multi_session_dates[session_idx] + " UTC"
                date_format = "%Y/%m/%d (%a) %H:%M UTC"
                date_string = datetime.strptime(date, date_format).replace(
                    tzinfo=timezone.utc
                )
                context += f"{msg['role']} (date: {date_string}): {msg['content']}\n"

        # Generate response
        start = time()
        hypothesis = await self.lme_response(context, question)
        duration = time() - start

        # Grade response
        grade = await self.lme_grader(question, gold_answer, hypothesis, question_type)

        result = {
            "question_id": question_id,
            "hypothesis": hypothesis,
            "question": question,
            "gold_answer": gold_answer,
            "context": context,
            "question_type": question_type,
            "duration": duration,
            "grade": grade,
        }

        return result, (1 if grade else 0), duration

    async def run_evaluation(
        self,
        df: pd.DataFrame,
        num_sessions: int = 500,
        batch_size: int = 5,
        baseline: bool = False,
        output_file: str = "longmemeval_results.jsonl",
    ):
        """Run the full evaluation pipeline with batched processing"""
        results = []
        correct_count = 0
        total_duration = 0
        total_retrieval_duration = 0

        eval_type = "baseline" if baseline else "Zep"
        self.logger.info(f"Starting {eval_type} evaluation")
        self.logger.info(
            f"Processing {num_sessions} sessions in batches of {batch_size}"
        )

        # Process in batches for efficiency
        for i in range(0, num_sessions, batch_size):
            batch_end = min(i + batch_size, num_sessions)
            batch_tasks = []

            # Create batch of evaluation tasks
            for j in range(i, batch_end):
                if baseline:
                    task = self.evaluate_conversation_baseline(df, j)
                else:
                    task = self.evaluate_conversation(df, j)
                batch_tasks.append(task)

            # Execute batch concurrently
            batch_results = await asyncio.gather(*batch_tasks)

            # Process results
            for result_data in batch_results:
                if baseline:
                    result, correct, duration = result_data
                    retrieval_duration = 0
                else:
                    result, correct, duration, retrieval_duration = result_data

                results.append(result)
                correct_count += correct
                total_duration += duration
                total_retrieval_duration += retrieval_duration

            self.logger.info(
                f"Processed batch {i // batch_size + 1}/{(num_sessions + batch_size - 1) // batch_size}"
            )

        # Save results
        with open(output_file, "w") as f:
            for result in results:
                f.write(json.dumps(result) + "\n")
        self.logger.info(f"Results saved to {output_file}")

        # Log summary
        accuracy = correct_count / num_sessions
        avg_duration = total_duration / num_sessions

        self.logger.info("Evaluation completed:")
        self.logger.info(f"  Accuracy: {accuracy:.2%} ({correct_count}/{num_sessions})")
        self.logger.info(f"  Average response time: {avg_duration:.2f}s")

        if not baseline:
            avg_retrieval_duration = total_retrieval_duration / num_sessions
            self.logger.info(f"  Average retrieval time: {avg_retrieval_duration:.2f}s")
