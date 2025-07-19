#!/usr/bin/env python3
"""
LongMemEval Benchmark Ingestion and Evaluation Script - Refactored
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
GOOGLE_DRIVE_FILE_ID = "1zJgtYRFhOh5zDQzzatiddfjYhFSnyQ80"

# Template for context composition
SEARCH_CONTEXT_TEMPLATE = """
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
"""
}


class Grade(BaseModel):
    is_correct: str = Field(description="yes or no")


class DatasetManager:
    """Handles dataset downloading and loading operations"""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
    
    async def download_dataset(
        self, file_path: str = os.path.join(DATA_PATH, "longmemeval_data.tar.gz")
    ) -> None:
        """Download and extract the LongMemEval dataset"""
        url = f"https://drive.google.com/uc?id={GOOGLE_DRIVE_FILE_ID}"

        self._ensure_data_directory()
        await self._download_if_missing(url, file_path)
        self._extract_if_missing(file_path)
    
    def _ensure_data_directory(self) -> None:
        """Create data directory if it doesn't exist"""
        if not os.path.exists(DATA_PATH):
            os.makedirs(DATA_PATH)
    
    async def _download_if_missing(self, url: str, file_path: str) -> None:
        """Download dataset file if not already present"""
        if not os.path.exists(file_path):
            self.logger.info(f"Downloading dataset to {file_path}...")
            gdown.download(url, file_path, quiet=False)
        else:
            self.logger.info(f"'{file_path}' already exists, skipping download.")
    
    def _extract_if_missing(self, file_path: str) -> None:
        """Extract dataset if not already extracted"""
        oracle_path = os.path.join(DATA_PATH, "longmemeval_oracle.json")
        if not os.path.exists(oracle_path):
            self.logger.info("Extracting dataset...")
            with tarfile.open(file_path, "r:gz") as tar:
                tar.extractall(path=DATA_PATH, filter="data")
        else:
            self.logger.info("'longmemeval_oracle.json' already exists, skipping extraction.")
    
    def load_dataset(self, dataset_path: str = "data/longmemeval_s.json") -> pd.DataFrame:
        """Load dataset from JSON file"""
        self.logger.info(f"Loading dataset from {dataset_path}")
        
        # Check if file exists in current directory, otherwise check parent directory
        if os.path.exists(dataset_path):
            return pd.read_json(dataset_path)
        
        parent_path = os.path.join("..", os.path.basename(dataset_path))
        if os.path.exists(parent_path):
            self.logger.info(f"Using dataset from parent directory: {parent_path}")
            return pd.read_json(parent_path)
        
        raise FileNotFoundError(f"Dataset not found at {dataset_path} or {parent_path}")


class LLMEvaluator:
    """Handles LLM-based response generation and grading"""
    
    def __init__(self, openai_client: AsyncOpenAI, logger: logging.Logger):
        self.oai_client = openai_client
        self.logger = logger
    
    async def generate_response(self, context: str, question: str) -> str:
        """Generate response using LLM with context"""
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
    
    async def grade_response(
        self, question: str, gold_answer: str, response: str, question_type: str
    ) -> bool:
        """Grade the response against gold standard"""
        system_prompt = """
        You are an expert grader that determines if answers to questions match a gold standard answer
        """

        prompt_template = GRADING_PROMPTS.get(question_type, GRADING_PROMPTS["default"])
        prompt = prompt_template.format(
            question=question, gold_answer=gold_answer, response=response
        )

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


class ZepIngester:
    """Handles data ingestion into Zep knowledge graph"""
    
    def __init__(self, zep_client: AsyncZep, logger: logging.Logger):
        self.zep = zep_client
        self.logger = logger
    
    async def ingest_dataset(
        self, df: pd.DataFrame, num_sessions: int = 500, question_type_filter: Optional[str] = None
    ) -> None:
        """Ingest conversation data into Zep knowledge graph"""
        filter_msg = f"question type: {question_type_filter}" if question_type_filter else "all question types"
        self.logger.info(f"Ingesting {num_sessions} sessions with {filter_msg}")

        for session_idx in range(num_sessions):
            if self._should_skip_session(df, session_idx, question_type_filter):
                continue
            
            await self._ingest_single_entry(df, session_idx)
    
    def _should_skip_session(self, df: pd.DataFrame, session_idx: int, question_type_filter: Optional[str]) -> bool:
        """Check if session should be skipped based on question type filter"""
        if question_type_filter:
            question_type = df["question_type"][session_idx]
            return question_type != question_type_filter
        return False
    
    async def _ingest_single_entry(self, df: pd.DataFrame, session_idx: int) -> None:
        """Ingest a single dataset entry with all its sessions"""
        try:
            multi_session = df["haystack_sessions"].iloc[session_idx]
            multi_session_dates = df["haystack_dates"].iloc[session_idx]
            question_type = df["question_type"][session_idx]
            
            self.logger.info(f"Processing session {session_idx}: {question_type}")
            
            user_id = f"lme_s_experiment_user_{session_idx}"
            await self.zep.user.add(user_id=user_id)
            
            await self._ingest_sessions_for_user(user_id, session_idx, multi_session, multi_session_dates)
            
        except Exception as e:
            self.logger.error(f"Error processing session {session_idx}: {e}")
    
    async def _ingest_sessions_for_user(
        self, user_id: str, session_idx: int, multi_session: list, multi_session_dates: list
    ) -> None:
        """Ingest all sessions for a specific user"""
        for sub_session_idx, session in enumerate(multi_session):
            session_id = f"lme_s_experiment_session_{session_idx}_{sub_session_idx}"
            
            await self.zep.memory.add_session(user_id=user_id, session_id=session_id)
            await self._ingest_messages_for_session(session_id, session, multi_session_dates[sub_session_idx])
    
    async def _ingest_messages_for_session(self, session_id: str, session: list, session_date: str) -> None:
        """Ingest all messages for a specific session"""
        for msg in session:
            message_payload = self._create_message_payload(msg, session_date)
            await self.zep.memory.add(session_id=session_id, messages=[message_payload])
    
    def _create_message_payload(self, msg: dict, session_date: str) -> Message:
        """Create a Zep Message payload from raw message data"""
        date = session_date + " UTC"
        date_format = "%Y/%m/%d (%a) %H:%M UTC"
        date_string = datetime.strptime(date, date_format).replace(tzinfo=timezone.utc)
        
        content = msg["content"]
        if len(content) > 8000:
            self.logger.warning(f"Message truncated from {len(content)} to 8000 characters")
            content = content[:8000]
        
        return Message(
            role=msg["role"],
            role_type=msg["role"],
            content=content,
            created_at=date_string.isoformat(),
        )


class SearchContextComposer:
    """Handles search context composition from Zep results"""
    
    @staticmethod
    def format_edge_date_range(edge: EntityEdge) -> str:
        """Format date range for edge display"""
        start_date = edge.valid_at if edge.valid_at else "date unknown"
        end_date = edge.invalid_at if edge.invalid_at else "present"
        return f"{start_date} - {end_date}"
    
    @staticmethod
    def compose_context(edges: List[EntityEdge], nodes: List[EntityNode]) -> str:
        """Compose search context from edges and nodes"""
        facts = [
            f"  - {edge.fact} ({SearchContextComposer.format_edge_date_range(edge)})" 
            for edge in edges
        ]
        entities = [f"  - {node.name}: {node.summary}" for node in nodes]
        
        return SEARCH_CONTEXT_TEMPLATE.format(
            facts="\n".join(facts), 
            entities="\n".join(entities)
        )


class LongMemEvaluator:
    """Main orchestrator for LongMemEval benchmark evaluation"""
    
    def __init__(self, zep_dev_environment: bool = False, log_level: str = "INFO"):
        load_dotenv()
        
        self.logger = self._setup_logger(log_level)
        
        # Initialize clients
        zep_client = self._create_zep_client(zep_dev_environment)
        openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        # Initialize components
        self.dataset_manager = DatasetManager(self.logger)
        self.zep_ingester = ZepIngester(zep_client, self.logger)
        self.llm_evaluator = LLMEvaluator(openai_client, self.logger)
        self.context_composer = SearchContextComposer()
        
        # Keep direct access to clients for backward compatibility
        self.zep = zep_client
    
    def _setup_logger(self, log_level: str) -> logging.Logger:
        """Setup and configure logger"""
        logger = logging.getLogger(__name__)
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        
        logger.setLevel(getattr(logging, log_level.upper()))
        return logger
    
    def _create_zep_client(self, zep_dev_environment: bool) -> AsyncZep:
        """Create and configure Zep client"""
        if zep_dev_environment:
            return AsyncZep(
                api_key=os.getenv("ZEP_API_KEY"),
                base_url="https://api.development.getzep.com/api/v2",
            )
        return AsyncZep(api_key=os.getenv("ZEP_API_KEY"))
    
    # Delegation methods for backward compatibility
    async def download_dataset(self, file_path: str = os.path.join(DATA_PATH, "longmemeval_data.tar.gz")) -> None:
        """Download and extract the LongMemEval dataset"""
        await self.dataset_manager.download_dataset(file_path)
    
    def load_dataset(self, dataset_path: str = "data/longmemeval_s.json") -> pd.DataFrame:
        """Load dataset from file"""
        return self.dataset_manager.load_dataset(dataset_path)
    
    async def ingest_data(
        self, df: pd.DataFrame, num_sessions: int = 500, question_type_filter: Optional[str] = None
    ) -> None:
        """Ingest conversation data into Zep knowledge graph"""
        await self.zep_ingester.ingest_dataset(df, num_sessions, question_type_filter)
    
    async def lme_response(self, context: str, question: str) -> str:
        """Generate response using LLM with context"""
        return await self.llm_evaluator.generate_response(context, question)
    
    async def lme_grader(
        self, question: str, gold_answer: str, response: str, question_type: str
    ) -> bool:
        """Grade the response against gold standard"""
        return await self.llm_evaluator.grade_response(question, gold_answer, response, question_type)
    
    def compose_search_context(self, edges: List[EntityEdge], nodes: List[EntityNode]) -> str:
        """Compose search context from edges and nodes"""
        return self.context_composer.compose_context(edges, nodes)
    
    async def evaluate_conversation(
        self, df: pd.DataFrame, multi_session_idx: int
    ) -> Tuple[dict, int, float, float]:
        """Evaluate a single conversation using Zep context"""
        question_id = df["question_id"][multi_session_idx]
        question_type = df["question_type"][multi_session_idx]
        question = f"(date: {df['question_date'][multi_session_idx]}) {df['question'][multi_session_idx]}"
        gold_answer = df["answer"][multi_session_idx]

        user_id = f"lme_s_experiment_user_{multi_session_idx}"

        start_retrieval = time()
        edges_results = await self.zep.graph.search(
            user_id=user_id, query=question, limit=20
        )
        nodes_results = await self.zep.graph.search(
            user_id=user_id, query=question, search_scope="nodes", limit=20
        )
        retrieval_duration = time() - start_retrieval

        context = self.compose_search_context(
            edges_results.edges or [], nodes_results.nodes or []
        )

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

        context = self._build_baseline_context(multi_session, multi_session_dates)

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
    
    def _build_baseline_context(self, multi_session: list, multi_session_dates: list) -> str:
        """Build context from full conversation history for baseline evaluation"""
        context = ""
        for session_idx, session in enumerate(multi_session):
            for msg in session:
                date = multi_session_dates[session_idx] + " UTC"
                date_format = "%Y/%m/%d (%a) %H:%M UTC"
                date_string = datetime.strptime(date, date_format).replace(
                    tzinfo=timezone.utc
                )
                context += f"{msg['role']} (date: {date_string}): {msg['content']}\n"
        return context

    async def run_evaluation(
        self,
        df: pd.DataFrame,
        num_sessions: int = 500,
        batch_size: int = 5,
        baseline: bool = False,
        output_file: str = "longmemeval_results.jsonl",
    ) -> None:
        """Run the full evaluation pipeline"""
        results = []
        correct_count = 0
        total_duration = 0
        total_retrieval_duration = 0

        self.logger.info(f"Starting {'baseline' if baseline else 'Zep'} evaluation")
        self.logger.info(f"Processing {num_sessions} sessions in batches of {batch_size}")

        for i in range(0, num_sessions, batch_size):
            batch_end = min(i + batch_size, num_sessions)
            batch_tasks = []

            for j in range(i, batch_end):
                if baseline:
                    task = self.evaluate_conversation_baseline(df, j)
                else:
                    task = self.evaluate_conversation(df, j)
                batch_tasks.append(task)

            batch_results = await asyncio.gather(*batch_tasks)

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

            self.logger.info(f"Processed batch {i//batch_size + 1}/{(num_sessions + batch_size - 1)//batch_size}")

        self._save_results(results, output_file)
        self._log_evaluation_summary(correct_count, num_sessions, total_duration, total_retrieval_duration, baseline)
    
    def _save_results(self, results: List[dict], output_file: str) -> None:
        """Save evaluation results to JSONL file"""
        with open(output_file, 'w') as f:
            for result in results:
                f.write(json.dumps(result) + '\n')
        self.logger.info(f"Results saved to {output_file}")
    
    def _log_evaluation_summary(
        self, correct_count: int, num_sessions: int, total_duration: float, 
        total_retrieval_duration: float, baseline: bool
    ) -> None:
        """Log evaluation summary statistics"""
        accuracy = correct_count / num_sessions
        avg_duration = total_duration / num_sessions
        
        self.logger.info(f"Evaluation completed:")
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

    evaluator = LongMemEvaluator(
        zep_dev_environment=args.zep_dev_environment, 
        log_level=args.log_level
    )

    # Download dataset
    if not args.skip_download:
        await evaluator.download_dataset()

    # Load dataset
    df = evaluator.load_dataset(args.dataset)

    # Ingest data
    if args.ingest and not args.baseline:
        await evaluator.ingest_data(df, args.num_sessions, args.question_type)

    # Run evaluation
    if args.eval:
        await evaluator.run_evaluation(
            df, 
            args.num_sessions, 
            args.batch_size, 
            args.baseline, 
            args.output
        )

if __name__ == "__main__":
    asyncio.run(main())
