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

# LLM Model Constants
RESPONSE_MODEL = "gpt-4o"
GRADER_MODEL = "gpt-4o"

DATA_PATH = "data"


class Grade(BaseModel):
    is_correct: str = Field(description="yes or no")


class LongMemEvalRunner:
    def __init__(self, zep_dev_environment: bool = False, dry_run: bool = False, log_level: str = "INFO"):
        load_dotenv()
        self.dry_run = dry_run
        
        # Setup logging
        self.logger = logging.getLogger(__name__)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
        
        self.logger.setLevel(getattr(logging, log_level.upper()))
        
        if not dry_run:
            if zep_dev_environment:
                self.zep = AsyncZep(
                    api_key=os.getenv("ZEP_API_KEY"),
                    base_url="https://api.development.getzep.com/api/v2",
                )
            else:
                self.zep = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))
            self.oai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        else:
            self.logger.info("DRY RUN MODE: Zep and OpenAI clients not initialized")
            self.zep = None
            self.oai_client = None
        self.template = """
FACTS and ENTITIES represent relevant context to the current conversation.

# These are the most relevant facts and their valid date ranges. If the fact is about an event, the event takes place during this time.
# format: FACT (Date range: from - to)
<FACTS>
{facts}
</FACTS>

# These are the most relevant entities
# ENTITY_NAME: entity summary
<ENTITIES>
{entities}
</ENTITIES>
"""

    async def download_dataset(
        self, file_path: str = os.path.join(DATA_PATH, "longmemeval_data.tar.gz")
    ):
        """Download and extract the LongMemEval dataset"""
        if self.dry_run:
            self.logger.info("DRY RUN: Would download and extract dataset")
            self.logger.info(f"DRY RUN: Target file: {file_path}")
            self.logger.info(f"DRY RUN: Would create directory: {DATA_PATH}")
            self.logger.info("DRY RUN: Would download from Google Drive")
            self.logger.info("DRY RUN: Would extract to longmemeval_oracle.json")
            return
            
        file_id = "1zJgtYRFhOh5zDQzzatiddfjYhFSnyQ80"
        url = f"https://drive.google.com/uc?id={file_id}"

        if not os.path.exists(DATA_PATH):
            os.makedirs(DATA_PATH)

        if not os.path.exists(file_path):
            self.logger.info(f"Downloading dataset to {file_path}...")
            gdown.download(url, file_path, quiet=False)
        else:
            self.logger.info(f"'{file_path}' already exists, skipping download.")

        if not os.path.exists(os.path.join(DATA_PATH, "longmemeval_oracle.json")):
            self.logger.info("Extracting dataset...")
            with tarfile.open(file_path, "r:gz") as tar:
                tar.extractall(path=DATA_PATH, filter="data")
        else:
            self.logger.info("'longmemeval_oracle.json' already exists, skipping extraction.")

    def load_dataset(
        self, dataset_option: str = "data/longmemeval_s.json"
    ) -> pd.DataFrame:
        """Load the LongMemEval dataset"""
        self.logger.info(f"Loading dataset from {dataset_option}")
        # Check if file exists in current directory, otherwise check parent directory
        if os.path.exists(dataset_option):
            return pd.read_json(dataset_option)
        else:
            parent_path = os.path.join("..", os.path.basename(dataset_option))
            if os.path.exists(parent_path):
                self.logger.info(f"Using dataset from parent directory: {parent_path}")
                return pd.read_json(parent_path)
            else:
                raise FileNotFoundError(
                    f"Dataset not found at {dataset_option} or {parent_path}"
                )
        return pd.read_json(dataset_option)


    async def ingest_data(
        self,
        df: pd.DataFrame,
        num_sessions: int = 500,
        question_type_filter: Optional[str] = None,
    ):
        """Ingest conversation data into Zep knowledge graph"""
        filter_msg = f"question type: {question_type_filter}" if question_type_filter else "all question types"
        self.logger.info(
            f"Ingesting {num_sessions} sessions with {filter_msg}"
        )

        for multi_session_idx in range(num_sessions):
            multi_session = df["haystack_sessions"].iloc[multi_session_idx]
            multi_session_dates = df["haystack_dates"].iloc[multi_session_idx]
            question_type = df["question_type"][multi_session_idx]

            if question_type_filter and question_type != question_type_filter:
                continue

            self.logger.info(f"Processing session {multi_session_idx}: {question_type}")

            user_id = f"lme_s_experiment_user_{multi_session_idx}"

            try:
                if self.dry_run:
                    self.logger.info(f"DRY RUN: Would create user: {user_id}")
                else:
                    await self.zep.user.add(user_id=user_id)

                for session_idx, session in enumerate(multi_session):
                    session_id = f"lme_s_experiment_session_{multi_session_idx}_{session_idx}"
                    
                    if self.dry_run:
                        self.logger.info(f"DRY RUN: Would create session: user_id={user_id}, session_id={session_id}")
                    else:
                        await self.zep.memory.add_session(
                            user_id=user_id,
                            session_id=session_id,
                        )
                    for msg in session:
                        date = multi_session_dates[session_idx] + " UTC"
                        date_format = "%Y/%m/%d (%a) %H:%M UTC"
                        date_string = datetime.strptime(date, date_format).replace(
                            tzinfo=timezone.utc
                        )
                        
                        if len(msg["content"]) > 8000:
                            self.logger.warning(
                                f"Message is over 8000 characters, truncating: {msg['content'][:100]}..."
                            )

                        message_payload = Message(
                            role=msg["role"],
                            role_type=msg["role"],
                            content=msg["content"][:8000],
                            created_at=date_string.isoformat(),
                        )
                        
                        if self.dry_run:
                            self.logger.debug(f"DRY RUN: Would add message to session {session_id}:")
                            self.logger.debug(f"  Role: {msg['role']}")
                            self.logger.debug(f"  Role Type: {msg['role']}")
                            self.logger.debug(f"  Content: {msg['content'][:100]}{'...' if len(msg['content']) > 100 else ''}")
                            self.logger.debug(f"  Created At: {date_string.isoformat()}")
                            self.logger.debug(f"  Session ID: {session_id}")
                        else:
                            await self.zep.memory.add(
                                session_id=session_id,
                                messages=[message_payload],
                            )
            except Exception as e:
                self.logger.error(f"Error processing session {multi_session_idx}: {e}")

    async def lme_response(self, context: str, question: str) -> str:
        """Generate response using LLM with context"""
        system_prompt = """
        You are a helpful expert assistant answering questions from lme_experiment users based on the provided context.
        """

        prompt = f"""
        Your task is to briefly answer the question. You are given the following context from the previous conversation. If you don't know how to answer the question, abstain from answering.
            <CONTEXT>
            {context}
            </CONTEXT>
            <QUESTION>
            {question}
            </QUESTION>

        Answer:
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
        """Grade the response against gold standard"""
        system_prompt = """
        You are an expert grader that determines if answers to questions match a gold standard answer
        """

        prompts = {
            "temporal-reasoning": f"""
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
            "knowledge-update": f"""
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
            "single-session-preference": f"""
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
            "default": f"""         
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

        prompt = prompts.get(question_type, prompts["default"])

        response = await self.oai_client.beta.chat.completions.parse(
            model=GRADER_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            response_format=Grade,
            temperature=0,
        )
        result = response.choices[0].message.parsed
        return result.is_correct.strip().lower() == "yes"

    def format_edge_date_range(self, edge: EntityEdge) -> str:
        """Format date range for edge display"""
        return f"{edge.valid_at if edge.valid_at else 'date unknown'} - {edge.invalid_at if edge.invalid_at else 'present'}"

    def compose_search_context(
        self, edges: List[EntityEdge], nodes: List[EntityNode]
    ) -> str:
        """Compose search context from edges and nodes"""
        facts = [
            f"  - {edge.fact} ({self.format_edge_date_range(edge)})" for edge in edges
        ]
        entities = [f"  - {node.name}: {node.summary}" for node in nodes]
        return self.template.format(
            facts="\n".join(facts), entities="\n".join(entities)
        )

    async def evaluate_conversation(
        self, df: pd.DataFrame, multi_session_idx: int
    ) -> Tuple[dict, int, float, float]:
        """Evaluate a single conversation with Zep knowledge graph"""
        user_id = f"lme_s_experiment_user_{multi_session_idx}"
        # session_id = f"lme_s_experiment_session_{multi_session_idx}" # unused

        question_id = df["question_id"][multi_session_idx]
        question_type = df["question_type"][multi_session_idx]
        question = f"(date: {df['question_date'][multi_session_idx]}) {df['question'][multi_session_idx]}"
        gold_answer = df["answer"][multi_session_idx]

        start = time()
        edges_results = (
            await self.zep.graph.search(
                user_id=user_id,
                reranker="cross_encoder",
                query=question[:255],
                scope="edges",
                limit=20,
            )
        ).edges

        node_results = (
            await self.zep.graph.search(
                user_id=user_id,
                reranker="rrf",
                query=question[:255],
                scope="nodes",
                limit=20,
            )
        ).nodes

        retrieval_duration = time() - start

        context = self.compose_search_context(edges_results, node_results)
        context_len = len(context.split(" "))

        hypothesis = await self.lme_response(context, question)
        duration = time() - start

        grade = await self.lme_grader(question, gold_answer, hypothesis, question_type)

        result = {
            "question_id": question_id,
            "hypothesis": hypothesis,
            "gold_answer": gold_answer,
            "context": context,
            "question_type": question_type,
            "context_len": context_len,
            "retrieval_duration": retrieval_duration,
            "duration": duration,
            "grade": grade,
        }

        return result, (1 if grade else 0), duration, retrieval_duration

    async def evaluate_conversation_baseline(
        self, df: pd.DataFrame, multi_session_idx: int
    ) -> Tuple[dict, int, float]:
        """Evaluate a single conversation with baseline (full context)"""
        question_id = df["question_id"][multi_session_idx]
        question_type = df["question_type"][multi_session_idx]
        question = f"(date: {df['question_date'][multi_session_idx]}) {df['question'][multi_session_idx]}"
        gold_answer = df["answer"][multi_session_idx]

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

        start = time()
        hypothesis = await self.lme_response(context, question)
        duration = time() - start

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
        use_baseline: bool = False,
    ):
        """Run the complete evaluation"""
        results = []
        grades = []
        durations = []
        retrieval_durations = []

        idx_start = 0
        while idx_start < min(num_sessions, len(df)) - 1:
            self.logger.info(f"Processing batch starting at index {idx_start}")

            if use_baseline:
                batch_results = await asyncio.gather(
                    *[
                        self.evaluate_conversation_baseline(df, multi_session_idx)
                        for multi_session_idx in range(
                            idx_start, min(idx_start + batch_size, num_sessions)
                        )
                    ]
                )

                for result, grade, duration in batch_results:
                    results.append(result)
                    grades.append(grade)
                    durations.append(duration)
                    retrieval_durations.append(0)  # No retrieval in baseline
            else:
                batch_results = await asyncio.gather(
                    *[
                        self.evaluate_conversation(df, multi_session_idx)
                        for multi_session_idx in range(
                            idx_start, min(idx_start + batch_size, num_sessions)
                        )
                    ]
                )

                for result, grade, duration, retrieval_duration in batch_results:
                    results.append(result)
                    grades.append(grade)
                    durations.append(duration)
                    retrieval_durations.append(retrieval_duration)

            idx_start += batch_size

        return results, grades, durations, retrieval_durations


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
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="Run data ingestion",
    )
    parser.add_argument(
        "--eval",
        action="store_true",
        help="Run evaluation",
    )
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
        "--dry-run",
        action="store_true",
        default=False,
        help="Dry run mode: print what would be done instead of executing",
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

    args = parser.parse_args()

    # Setup basic logging for main function
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    
    # Check if at least one action is specified
    if not args.ingest and not args.eval:
        parser.print_help()
        logger.error("Error: You must specify at least one action: --ingest or --eval")
        return

    runner = LongMemEvalRunner(
        zep_dev_environment=args.zep_dev_environment, 
        dry_run=args.dry_run,
        log_level=args.log_level
    )

    # Download dataset
    if not args.skip_download:
        await runner.download_dataset()

    # Load dataset
    df = runner.load_dataset(args.dataset)

    # Ingest data
    if args.ingest and not args.baseline:
        await runner.ingest_data(df, args.num_sessions, args.question_type)

    # Run evaluation
    if args.eval:
        results, grades, durations, retrieval_durations = await runner.run_evaluation(
            df, args.num_sessions, args.batch_size, args.baseline
        )

        # Save results
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)

        # Print summary
        accuracy = sum(grades) / len(grades) if grades else 0
        avg_duration = sum(durations) / len(durations) if durations else 0
        avg_retrieval_duration = (
            sum(retrieval_durations) / len(retrieval_durations)
            if retrieval_durations
            else 0
        )

        logger.info("Evaluation Results:")
        logger.info(f"Total questions: {len(grades)}")
        logger.info(f"Correct answers: {sum(grades)}")
        logger.info(f"Accuracy: {accuracy:.3f}")
        logger.info(f"Average duration: {avg_duration:.3f}s")
        if not args.baseline:
            logger.info(f"Average retrieval duration: {avg_retrieval_duration:.3f}s")
        logger.info(f"Results saved to: {args.output}")

    if args.ingest:
        logger.info("Data ingestion completed successfully")


if __name__ == "__main__":
    asyncio.run(main())
