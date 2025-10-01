#!/usr/bin/env python3
"""
Evaluation module for LongMemEval benchmark
"""

import asyncio
from time import time

import pandas as pd
import tiktoken
from zep_cloud import EntityEdge, EntityNode, Episode

from clients import create_openai_client, create_zep_client
from common import EvaluationResult, Grade
from config import BenchmarkConfig
from utils import setup_logging

# Grading prompts
GRADING_PROMPTS = {
    "temporal-reasoning": """
I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. In addition, do not penalize off-by-one errors for the number of days. If the question asks for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 days when the answer is 18), the model's response is still correct.

<QUESTION>
{question}
</QUESTION>
<CORRECT ANSWER>
{gold_answer}
</CORRECT ANSWER>
<RESPONSE>
{response}
</RESPONSE>
""",
    "knowledge-update": """
I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response contains some previous information along with an updated answer, the response should be considered as correct as long as the updated answer is the required answer.

<QUESTION>
{question}
</QUESTION>
<CORRECT ANSWER>
{gold_answer}
</CORRECT ANSWER>
<RESPONSE>
{response}
</RESPONSE>
""",
    "single-session-preference": """
I will give you a question, a rubric for desired personalized response, and a response from a model. Please answer yes if the response satisfies the desired response. Otherwise, answer no. The model does not need to reflect all the points in the rubric. The response is correct as long as it recalls and utilizes the user's personal information correctly.

<QUESTION>
{question}
</QUESTION>
<RUBRIC>
{gold_answer}
</RUBRIC>
<RESPONSE>
{response}
</RESPONSE>
""",
    "default": """
I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no.

<QUESTION>
{question}
</QUESTION>
<CORRECT ANSWER>
{gold_answer}
</CORRECT ANSWER>
<RESPONSE>
{response}
</RESPONSE>
""",
}

CONTEXT_TEMPLATE = """
The following sections contain relevant information for the current conversation.

<FACTS>
# Timestamped Facts
These facts include datetime stamps that indicate WHEN the actual event occurred.

{facts}
</FACTS>

<ENTITIES>
# Key People, Places, and Things
These are important entities referenced in the conversation.

{entities}
</ENTITIES>

<MESSAGES>
# Relevant Historical Messages
These are the most relevant messages from the user's interaction history.

{messages}
</MESSAGES>
"""


class EvaluationRunner:
    def __init__(
        self,
        log_level: str = "WARNING",
        config: BenchmarkConfig | None = None,
    ):
        self.logger = setup_logging(log_level, __name__)

        # Initialize clients using factory functions
        self.zep = create_zep_client()
        self.oai_client = create_openai_client()

        # Set up configuration with defaults
        if config is None:
            from config import GraphParams, ModelConfig

            config = BenchmarkConfig(graph_params=GraphParams(), models=ModelConfig())

        # Graph retrieval parameters
        self.edge_limit = config.graph_params.edge_limit
        self.node_limit = config.graph_params.node_limit
        self.episode_limit = config.graph_params.episode_limit
        self.edge_reranker = config.graph_params.edge_reranker
        self.node_reranker = config.graph_params.node_reranker
        self.episode_reranker = config.graph_params.episode_reranker

        # Model configurations
        self.response_model = config.models.response_model
        self.grader_model = config.models.grader_model

        # Model parameters
        self.temperature = config.models.temperature
        self.max_tokens = config.models.max_tokens
        self.reasoning_effort = config.models.reasoning_effort
        self.max_completion_tokens = config.models.max_completion_tokens

        # Initialize tokenizer for context length measurement
        try:
            self.tokenizer = tiktoken.encoding_for_model(self.response_model)
        except Exception:
            # Fallback to cl100k_base encoding if model-specific encoding not available
            self.tokenizer = tiktoken.get_encoding("cl100k_base")

    def _count_tokens(self, text: str) -> int:
        """Count tokens in text using the configured tokenizer"""
        try:
            return len(self.tokenizer.encode(text))
        except Exception as e:
            self.logger.warning(f"Token counting failed: {e}")
            return 0

    def _is_reasoning_model(self, model: str) -> bool:
        """Check if a model is a reasoning model (GPT-5, o1, o3)"""
        model_lower = model.lower()
        return any(prefix in model_lower for prefix in ["gpt-5", "o1", "o3"])

    def _build_completion_params(self, model: str, messages: list) -> dict:
        """Build API parameters based on model type"""
        params = {
            "model": model,
            "messages": messages,
        }

        if self._is_reasoning_model(model):
            # Reasoning models use different parameters
            if self.reasoning_effort:
                params["reasoning_effort"] = self.reasoning_effort
            if self.max_completion_tokens:
                params["max_completion_tokens"] = self.max_completion_tokens
        else:
            # Traditional models
            params["temperature"] = self.temperature
            if self.max_tokens:
                params["max_tokens"] = self.max_tokens

        return params

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

        CONTEXT:
        {context}

        QUESTION:
        {question}
        """

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        params = self._build_completion_params(self.response_model, messages)
        response = await self.oai_client.chat.completions.create(**params)
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
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        # Build base parameters
        params = self._build_completion_params(self.grader_model, messages)
        params["response_format"] = Grade

        completion = await self.oai_client.beta.chat.completions.parse(**params)

        result = completion.choices[0].message.parsed
        if result is None:
            return False
        return result.is_correct.strip().lower() == "yes"

    def compose_context(
        self,
        edges: list[EntityEdge],
        nodes: list[EntityNode],
        episodes: list[Episode],
    ) -> str:
        """Compose context from Zep graph retrieval results"""
        # Format facts with date ranges
        facts = []
        for edge in edges:
            start_date = edge.valid_at if edge.valid_at else "date unknown"
            facts.append(f"({start_date}) {edge.fact}")

        # Format entities
        entities = [
            f"{node.name} ({', '.join(node.labels) if node.labels else ''}): {node.summary}"
            for node in nodes
        ]

        # Format episodes
        episodes_content = [episode.content for episode in episodes]

        return CONTEXT_TEMPLATE.format(
            facts="\n".join(facts),
            entities="\n".join(entities),
            messages="\n".join(episodes_content),
        )

    async def evaluate_conversation(
        self, df: pd.DataFrame, user_idx: int
    ) -> tuple[EvaluationResult, int, float, float]:
        """Evaluate a single conversation using Zep context retrieval"""
        # Extract question data
        question_id = str(df["question_id"][user_idx])
        question_type = str(df["question_type"][user_idx])
        question = f"(date: {df['question_date'][user_idx]}) {df['question'][user_idx]}"
        gold_answer = str(df["answer"][user_idx])
        user_id = f"lme_s_experiment_user_{user_idx}"

        # Retrieve relevant context from Zep graph
        start_retrieval = time()

        # Prepare retrieval tasks
        retrieval_tasks = []

        # Edge retrieval
        edge_kwargs = {
            "user_id": user_id,
            "query": question,
            "scope": "edges",
            "limit": self.edge_limit,
            "reranker": self.edge_reranker,
        }
        retrieval_tasks.append(self.zep.graph.search(**edge_kwargs))

        # Node retrieval
        node_kwargs = {
            "user_id": user_id,
            "query": question,
            "scope": "nodes",
            "limit": self.node_limit,
            "reranker": self.node_reranker,
        }
        retrieval_tasks.append(self.zep.graph.search(**node_kwargs))

        # Episode retrieval (only if limit > 0)
        if self.episode_limit > 0:
            episode_kwargs = {
                "user_id": user_id,
                "query": question,
                "scope": "episodes",
                "limit": self.episode_limit,
                "reranker": self.episode_reranker,
            }
            retrieval_tasks.append(self.zep.graph.search(**episode_kwargs))
        else:
            # Create empty episode result when disabled
            async def empty_episodes():
                from types import SimpleNamespace

                return SimpleNamespace(episodes=[])

            retrieval_tasks.append(empty_episodes())

        edges_results, nodes_results, episodes_results = await asyncio.gather(*retrieval_tasks)
        retrieval_duration = time() - start_retrieval

        # Extract results
        final_edges = edges_results.edges or []
        final_nodes = nodes_results.nodes or []
        final_episodes = episodes_results.episodes if hasattr(episodes_results, "episodes") else []

        # Compose context from retrieval results
        context = self.compose_context(final_edges, final_nodes, final_episodes)

        # Generate response
        start = time()
        hypothesis = await self.lme_response(context, question)
        duration = time() - start

        # Grade response
        grade = await self.lme_grader(question, gold_answer, hypothesis, question_type)

        # Count context tokens and characters
        context_tokens = self._count_tokens(context)
        context_chars = len(context)

        result = EvaluationResult(
            user_id=user_id,
            question_id=question_id,
            question=question,
            question_type=question_type,
            hypothesis=hypothesis,
            gold_answer=gold_answer,
            context=context,
            context_tokens=context_tokens,
            context_chars=context_chars,
            duration=duration,
            grade=grade,
        )

        return result, (1 if grade else 0), duration, retrieval_duration
