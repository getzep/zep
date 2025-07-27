#!/usr/bin/env python3
"""
Summarization utilities for LongMemEval
"""

import asyncio
import logging
from typing import List

from openai import AsyncOpenAI
from zep_cloud import EntityEdge, EntityNode, Episode
from zep_cloud.client import AsyncZep

from common import (
    SUMMARY_MODEL,
    CONTEXT_TEMPLATE_SUMMARY,
    get_summarization_prompts,
)


class SummarizationService:
    def __init__(
        self,
        zep_client: AsyncZep,
        oai_client: AsyncOpenAI,
        summary_model: str = SUMMARY_MODEL,
    ):
        self.zep = zep_client
        self.oai_client = oai_client
        self.summary_model = summary_model
        self.logger = logging.getLogger(__name__)

        # Cache for node UUID to name mappings
        self._node_uuid_cache = {}
        # Cache for in-flight requests to prevent duplicate API calls
        self._node_uuid_tasks = {}

    async def resolve_node_uuid_to_name(self, node_uuid: str) -> str:
        """Resolve node uuid to node name with concurrency-safe caching"""
        # Check if result is already cached
        if node_uuid in self._node_uuid_cache:
            return self._node_uuid_cache[node_uuid]

        # Check if there's already a task in flight for this UUID
        if node_uuid in self._node_uuid_tasks:
            return await self._node_uuid_tasks[node_uuid]

        # Create and cache the task for this UUID
        async def fetch_node_name():
            try:
                node = await self.zep.graph.node.get(node_uuid)
                node_name = node.name
                # Cache the result
                self._node_uuid_cache[node_uuid] = node_name
                return node_name
            finally:
                # Clean up the task cache
                self._node_uuid_tasks.pop(node_uuid, None)

        # Store the task and await it
        task = asyncio.create_task(fetch_node_name())
        self._node_uuid_tasks[node_uuid] = task

        return await task

    async def summarize_context(
        self, question: str, facts: str, entities: str, messages: str, strategy: str = "list"
    ) -> str:
        """Summarize the context using LLM with specified strategy"""
        
        # Get the appropriate prompts for the strategy
        system_prompt, user_prompt_template = get_summarization_prompts(strategy, question)
        
        if system_prompt is None or user_prompt_template is None:
            # No summarization requested
            return ""
        
        # Format the user prompt
        if strategy == "qa":
            prompt = user_prompt_template.format(
                question=question, facts=facts, entities=entities, messages=messages
            )
        else:  # strategy == "list"
            prompt = user_prompt_template.format(
                facts=facts, entities=entities, messages=messages
            )

        response = await self.oai_client.chat.completions.create(
            model=self.summary_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )

        return response.choices[0].message.content or ""

    async def compose_search_context_with_summary(
        self,
        question: str,
        edges: List[EntityEdge],
        nodes: List[EntityNode],
        episodes: List[Episode],
        strategy: str = "list",
    ) -> str:
        """Compose context from Zep search results with summarization"""
        # Format facts with date ranges
        facts = []
        for edge in edges:
            start_date = edge.valid_at if edge.valid_at else "date unknown"
            end_date = edge.invalid_at if edge.invalid_at else "present"
            # Optionally include node names if needed
            # source_node_name = await self.resolve_node_uuid_to_name(edge.source_node_uuid)
            # target_node_name = await self.resolve_node_uuid_to_name(edge.target_node_uuid)
            # facts.append(f"  - ({source_node_name} -> {target_node_name}): {edge.fact} ({start_date} - {end_date})")
            facts.append(f"  - {edge.fact} (from: {start_date} - to: {end_date})")

        # Format entities
        entities = [f"  - {node.name}: {node.summary}" for node in nodes]

        messages = [f"({episode.content}\n" for episode in episodes]

        # Summarize context
        summary = await self.summarize_context(
            question, "\n".join(facts), "\n".join(entities), "\n".join(messages), strategy
        )

        return CONTEXT_TEMPLATE_SUMMARY.format(summary=summary)
