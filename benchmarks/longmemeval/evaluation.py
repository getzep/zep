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
    SUMMARY_MODEL,
    GRADING_PROMPTS,
    Grade,
    CONTEXT_TEMPLATE,
    get_summarization_prompts,
)
from summarization import SummarizationService
from reranker import RerankerFactory


class EvaluationRunner:
    def __init__(
        self,
        zep_dev_environment: bool = False,
        log_level: str = "INFO",
        config: dict = None,
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

        # Set up configuration with defaults
        self.config = config or {}

        # Search parameters (with defaults matching current behavior)
        self.edge_limit = self.config.get("edge_limit", 30)
        self.node_limit = self.config.get("node_limit", 30)
        self.episode_limit = self.config.get("episode_limit", 20)
        self.edge_reranker = self.config.get("edge_reranker", "cross_encoder")
        self.node_reranker = self.config.get("node_reranker", None)
        self.episode_reranker = self.config.get("episode_reranker", None)

        # Model configurations
        self.response_model = self.config.get("response_model", RESPONSE_MODEL)
        self.grader_model = self.config.get("grader_model", GRADER_MODEL)
        self.summary_model = self.config.get("summary_model", SUMMARY_MODEL)

        # Initialize summarization service with configurable model
        self.summarization_service = SummarizationService(
            self.zep, self.oai_client, summary_model=self.summary_model
        )
        self.summarization_strategy = self.config.get("strategy", None)

        # Initialize reranker service if needed
        self.reranker_service = None
        reranker_types = [self.edge_reranker, self.node_reranker, self.episode_reranker]
        secondary_rerankers = [
            rt for rt in reranker_types if RerankerFactory.is_secondary_reranker(rt)
        ]

        if secondary_rerankers:
            # Use the first secondary reranker found (they should all be the same)
            reranker_type = secondary_rerankers[0]
            try:
                self.reranker_service = RerankerFactory.create_reranker(reranker_type)
                self.logger.info(f"Initialized {reranker_type} service")
            except Exception as e:
                self.logger.error(f"Failed to initialize {reranker_type} service: {e}")
                # Fall back to None - will disable secondary reranking
                self.reranker_service = None

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
            model=self.response_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=1 if self.response_model == "o4-mini" else 0,
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
            model=self.grader_model,
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
            f"{node.name} ({', '.join(node.labels) if node.labels else ''}): {node.summary} ({node.attributes if node.attributes else ''})"
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
            question, edges, nodes, episodes, strategy=self.summarization_strategy
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

        # Prepare search tasks based on configuration
        search_tasks = []

        # Edge search
        edge_kwargs = {
            "user_id": user_id,
            "query": question,
        }
        # Handle secondary rerankers: fetch 3x results without Zep reranker (max 50)
        if RerankerFactory.is_secondary_reranker(self.edge_reranker):
            desired_limit = self.edge_limit * 3
            actual_limit = min(desired_limit, 50)
            if desired_limit > 50:
                self.logger.warning(
                    f"Edge {self.edge_reranker}: wanted {desired_limit} results, capped at 50 due to Zep API limit"
                )
            edge_kwargs["limit"] = actual_limit
            # Don't pass reranker parameter to Zep
        else:
            edge_kwargs["limit"] = min(self.edge_limit, 50)
            if self.edge_reranker:
                edge_kwargs["reranker"] = self.edge_reranker
        search_tasks.append(self._search_zep_with_retry(**edge_kwargs))

        # Node search
        node_kwargs = {
            "user_id": user_id,
            "query": question,
            "scope": "nodes",
        }
        # Handle secondary rerankers: fetch 3x results without Zep reranker (max 50)
        if RerankerFactory.is_secondary_reranker(self.node_reranker):
            desired_limit = self.node_limit * 3
            actual_limit = min(desired_limit, 50)
            if desired_limit > 50:
                self.logger.warning(
                    f"Node {self.node_reranker}: wanted {desired_limit} results, capped at 50 due to Zep API limit"
                )
            node_kwargs["limit"] = actual_limit
            # Don't pass reranker parameter to Zep
        else:
            node_kwargs["limit"] = min(self.node_limit, 50)
            if self.node_reranker:
                node_kwargs["reranker"] = self.node_reranker
        search_tasks.append(self._search_zep_with_retry(**node_kwargs))

        # Episode search (only if limit > 0)
        if self.episode_limit > 0:
            episode_kwargs = {
                "user_id": user_id,
                "query": question,
                "scope": "episodes",
            }
            # Handle secondary rerankers: fetch 3x results without Zep reranker (max 50)
            if RerankerFactory.is_secondary_reranker(self.episode_reranker):
                desired_limit = self.episode_limit * 3
                actual_limit = min(desired_limit, 50)
                if desired_limit > 50:
                    self.logger.warning(
                        f"Episode {self.episode_reranker}: wanted {desired_limit} results, capped at 50 due to Zep API limit"
                    )
                episode_kwargs["limit"] = actual_limit
                # Don't pass reranker parameter to Zep
            else:
                episode_kwargs["limit"] = min(self.episode_limit, 50)
                if self.episode_reranker:
                    episode_kwargs["reranker"] = self.episode_reranker
            search_tasks.append(self._search_zep_with_retry(**episode_kwargs))
        else:
            # Create empty episode result when disabled
            async def empty_episodes():
                from types import SimpleNamespace

                return SimpleNamespace(episodes=[])

            search_tasks.append(empty_episodes())

        edges_results, nodes_results, episodes_results = await asyncio.gather(
            *search_tasks
        )
        retrieval_duration = time() - start_retrieval

        # Apply secondary reranking if configured and extract results
        final_edges = edges_results.edges or []
        final_nodes = nodes_results.nodes or []
        final_episodes = (
            episodes_results.episodes if hasattr(episodes_results, "episodes") else []
        )

        if self.reranker_service:
            # Rerank edges if needed
            if (
                RerankerFactory.is_secondary_reranker(self.edge_reranker)
                and final_edges
            ):
                final_edges = self.reranker_service.rerank_edges(
                    question, final_edges, self.edge_limit
                )

            # Rerank nodes if needed
            if (
                RerankerFactory.is_secondary_reranker(self.node_reranker)
                and final_nodes
            ):
                final_nodes = self.reranker_service.rerank_nodes(
                    question, final_nodes, self.node_limit
                )

            # Rerank episodes if needed
            if (
                RerankerFactory.is_secondary_reranker(self.episode_reranker)
                and final_episodes
            ):
                final_episodes = self.reranker_service.rerank_episodes(
                    question, final_episodes, self.episode_limit
                )

        # Compose context from search results
        if self.summarization_strategy:
            context = await self.compose_search_context_with_summary(
                question,
                final_edges,
                final_nodes,
                final_episodes,
            )
        else:
            context = self.compose_search_context(
                final_edges,
                final_nodes,
                final_episodes,
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
            "evaluation_type": "zep",  # Mark as Zep evaluation
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
        user_id = f"lme_s_experiment_user_{multi_session_idx}"

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

        # Generate response using configured models
        start = time()
        hypothesis = await self.lme_response(context, question)
        duration = time() - start

        # Grade response using configured models
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
            "evaluation_type": "baseline",  # Mark as baseline evaluation
        }

        return result, (1 if grade else 0), duration
