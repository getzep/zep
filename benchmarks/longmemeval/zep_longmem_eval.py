#!/usr/bin/env python3
"""
LongMemEval Benchmark Ingestion and Evaluation Script
"""

import asyncio
import argparse
import json
import logging
import os
import tarfile
from datetime import datetime, timezone
from time import time
from typing import List, Tuple, Optional

import gdown
import pandas as pd
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from zep_cloud import EntityEdge, EntityNode, Message
from zep_cloud.client import AsyncZep

# Configuration Constants
RESPONSE_MODEL = "gpt-4o"
GRADER_MODEL = "gpt-4o"
DATA_PATH = "data"

# Context template for search results
CONTEXT_TEMPLATE = """
FACTS and ENTITIES represent relevant context to the current conversation.

# These are the most relevant facts for the conversation along with the datetime of the event that the fact refers to.
If a fact mentions something happening a week ago, then the datetime will be the datetime of last week and not the datetime
of when the fact was stated.
Timestamps in memories represent the actual time the event occurred, not the time the event was mentioned in a message.
    
<FACTS>
{facts}
</FACTS>

# These are the most relevant entities
# ENTITY_NAME: entity summary
<ENTITIES>
{entities}
</ENTITIES>
"""

# Grading prompts by question type
GRADING_PROMPTS = {
    "temporal-reasoning": """
I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. In addition, do not penalize off-by-one errors for the number of days. If the question asks for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 days when the answer is 18), the model's response is still correct.

<QUESTION>
B: {question}
</QUESTION>
<CORRECT ANSWER>
{gold_answer}
</CORRECT ANSWER>
<RESPONSE>
A: {response}
</RESPONSE>
""",
    "knowledge-update": """
I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response contains some previous information along with an updated answer, the response should be considered as correct as long as the updated answer is the required answer.

<QUESTION>
B: {question}
</QUESTION>
<CORRECT ANSWER>
{gold_answer}
</CORRECT ANSWER>
<RESPONSE>
A: {response}
</RESPONSE>
""",
    "single-session-preference": """
I will give you a question, a rubric for desired personalized response, and a response from a model. Please answer yes if the response satisfies the desired response. Otherwise, answer no. The model does not need to reflect all the points in the rubric. The response is correct as long as it recalls and utilizes the user's personal information correctly.

<QUESTION>
B: {question}
</QUESTION>
<RUBRIC>
{gold_answer}
</RUBRIC>
<RESPONSE>
A: {response}
</RESPONSE>
""",
    "default": """
I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no.

<QUESTION>
B: {question}
</QUESTION>
<CORRECT ANSWER>
{gold_answer}
</CORRECT ANSWER>
<RESPONSE>
A: {response}
</RESPONSE>
""",
}


class Grade(BaseModel):
    is_correct: str = Field(description="yes or no")


class LongMemEvalRunner:
    def __init__(self, zep_dev_environment: bool = False, log_level: str = "INFO"):
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

    async def download_dataset(
        self, file_path: str = os.path.join(DATA_PATH, "longmemeval_data.tar.gz")
    ):
        """Download and extract the LongMemEval dataset from Google Drive"""
        file_id = "1zJgtYRFhOh5zDQzzatiddfjYhFSnyQ80"
        url = f"https://drive.google.com/uc?id={file_id}"

        # Create data directory
        if not os.path.exists(DATA_PATH):
            os.makedirs(DATA_PATH)

        # Download if needed
        if not os.path.exists(file_path):
            self.logger.info(f"Downloading dataset to {file_path}...")
            gdown.download(url, file_path, quiet=False)
        else:
            self.logger.info(f"'{file_path}' already exists, skipping download.")

        # Extract if needed
        oracle_path = os.path.join(DATA_PATH, "longmemeval_oracle.json")
        if not os.path.exists(oracle_path):
            self.logger.info("Extracting dataset...")
            with tarfile.open(file_path, "r:gz") as tar:
                tar.extractall(path=DATA_PATH, filter="data")
        else:
            self.logger.info(
                "'longmemeval_oracle.json' already exists, skipping extraction."
            )

    def load_dataset(
        self, dataset_path: str = "data/longmemeval_s.json"
    ) -> pd.DataFrame:
        """Load the LongMemEval dataset from JSON file"""
        self.logger.info(f"Loading dataset from {dataset_path}")

        # Check current directory first, then parent
        if os.path.exists(dataset_path):
            return pd.read_json(dataset_path)

        parent_path = os.path.join("..", os.path.basename(dataset_path))
        if os.path.exists(parent_path):
            self.logger.info(f"Using dataset from parent directory: {parent_path}")
            return pd.read_json(parent_path)

        raise FileNotFoundError(f"Dataset not found at {dataset_path} or {parent_path}")

    async def ingest_data(
        self,
        df: pd.DataFrame,
        num_sessions: int = 500,
        question_type_filter: Optional[str] = None,
        start_index: int = 0,
    ):
        """Ingest conversation data into Zep knowledge graph"""
        filter_msg = (
            f"question type: {question_type_filter}"
            if question_type_filter
            else "all question types"
        )
        # Ensure we don't exceed dataset bounds
        max_sessions = len(df)
        end_index = min(start_index + num_sessions, max_sessions)
        actual_sessions = end_index - start_index

        self.logger.info(
            f"Ingesting {actual_sessions} sessions (indices {start_index}-{end_index - 1}) with {filter_msg}"
        )

        for multi_session_idx in range(start_index, end_index):
            # Get session data
            multi_session = df["haystack_sessions"].iloc[multi_session_idx]
            multi_session_dates = df["haystack_dates"].iloc[multi_session_idx]
            question_type = df["question_type"][multi_session_idx]

            # Apply question type filter
            if question_type_filter and question_type != question_type_filter:
                continue

            self.logger.info(f"Processing session {multi_session_idx}: {question_type}")

            try:
                # Create user
                user_id = f"lme_s_experiment_user_{multi_session_idx}"
                await self.zep.user.add(user_id=user_id)

                # Process each session for this user
                for session_idx, session in enumerate(multi_session):
                    session_id = (
                        f"lme_s_experiment_session_{multi_session_idx}_{session_idx}"
                    )

                    # Create Zep session
                    await self.zep.memory.add_session(
                        user_id=user_id, session_id=session_id
                    )

                    # Add messages to session
                    for msg in session:
                        # Parse and format timestamp
                        date = multi_session_dates[session_idx] + " UTC"
                        date_format = "%Y/%m/%d (%a) %H:%M UTC"
                        date_string = datetime.strptime(date, date_format).replace(
                            tzinfo=timezone.utc
                        )

                        # Create message payload
                        message_payload = Message(
                            role=msg["role"],
                            role_type=msg["role"],
                            content=msg["content"],
                            created_at=date_string.isoformat(),
                        )

                        # Add to Zep
                        await self.zep.memory.add(
                            session_id=session_id,
                            messages=[message_payload],
                        )

            except Exception as e:
                self.logger.error(f"Error processing session {multi_session_idx}: {e}")

    async def lme_response(self, context: str, question: str) -> str:
        """Generate response using LLM with provided context"""
        system_prompt = """
        You are a helpful expert assistant answering questions from lme_experiment users based on the provided context.
        """

        prompt = f"""
        Your task is to briefly answer the question. You are given the following context from the previous conversation. If you don't know how to answer the question, abstain from answering.
        
        Context: {context}
        
        Question: {question}
        """

        response = await self.oai_client.chat.completions.create(
            model=RESPONSE_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
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
        self, edges: List[EntityEdge], nodes: List[EntityNode]
    ) -> str:
        """Compose context from Zep search results"""
        # Format facts with date ranges
        facts = []
        for edge in edges:
            start_date = edge.valid_at if edge.valid_at else "date unknown"
            end_date = edge.invalid_at if edge.invalid_at else "present"
            facts.append(f"  - {edge.fact} ({start_date} - {end_date})")

        # Format entities
        entities = [f"  - {node.name}: {node.summary}" for node in nodes]

        return CONTEXT_TEMPLATE.format(
            facts="\n".join(facts), entities="\n".join(entities)
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
        edges_results = await self.zep.graph.search(
            user_id=user_id, query=question, limit=20, reranker="cross_encoder"
        )
        nodes_results = await self.zep.graph.search(
            user_id=user_id, query=question, scope="nodes", limit=20, reranker="rrf"
        )
        retrieval_duration = time() - start_retrieval

        # Compose context from search results
        context = self.compose_search_context(
            edges_results.edges or [], nodes_results.nodes or []
        )

        # Generate response
        start = time()
        hypothesis = await self.lme_response(context, question)
        duration = time() - start

        # Grade response
        grade = await self.lme_grader(question, gold_answer, hypothesis, question_type)

        result = {
            "question_id": question_id,
            "hypothesis": hypothesis,
            "gold_answer": gold_answer,
            "context": context,
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


async def main():
    parser = argparse.ArgumentParser(description="Run LongMemEval evaluation")
    parser.add_argument(
        "--dataset",
        default="data/longmemeval_s.json",
        help="Dataset file path (default: data/longmemeval_s.json)",
    )
    parser.add_argument(
        "--num-sessions",
        type=int,
        default=500,
        help="Number of sessions to process (default: 500)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Batch size for processing (default: 5)",
    )
    parser.add_argument("--ingest", action="store_true", help="Run data ingestion")
    parser.add_argument("--eval", action="store_true", help="Run evaluation")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Run baseline evaluation instead of Zep evaluation",
    )
    parser.add_argument(
        "--skip-download", action="store_true", help="Skip dataset download"
    )
    parser.add_argument(
        "--output",
        default="longmemeval_results.jsonl",
        help="Output file path (default: longmemeval_results.jsonl)",
    )
    parser.add_argument(
        "--zep-dev-environment",
        action="store_true",
        default=False,
        help="Use Zep development environment (default: production)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level (default: INFO)",
    )
    parser.add_argument(
        "--question-type",
        default=None,
        help="Filter by question type (default: None - ingest all types)",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=0,
        help="Start ingestion from this index (default: 0)",
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)

    # Check if at least one action is specified
    if not args.ingest and not args.eval:
        parser.print_help()
        logger.error("Error: You must specify at least one action: --ingest or --eval")
        return

    # Initialize runner
    runner = LongMemEvalRunner(
        zep_dev_environment=args.zep_dev_environment, log_level=args.log_level
    )

    # Download dataset
    if not args.skip_download:
        await runner.download_dataset()

    # Load dataset
    df = runner.load_dataset(args.dataset)

    # Ingest data
    if args.ingest and not args.baseline:
        await runner.ingest_data(
            df, args.num_sessions, args.question_type, args.start_index
        )

    # Run evaluation
    if args.eval:
        await runner.run_evaluation(
            df, args.num_sessions, args.batch_size, args.baseline, args.output
        )


if __name__ == "__main__":
    asyncio.run(main())
