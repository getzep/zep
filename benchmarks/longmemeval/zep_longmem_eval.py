#!/usr/bin/env python3
"""
LongMemEval standalone script - refactored from zep_longmem_eval.ipynb

This script evaluates agent memory capabilities using the LongMemEval dataset
with Zep's temporal knowledge graph architecture.
"""

import asyncio
import argparse
import json
import os
import tarfile
from datetime import datetime, timezone
from time import time
from typing import List, Tuple

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


class Grade(BaseModel):
    is_correct: str = Field(description="yes or no")


class LongMemEvalRunner:
    def __init__(self, zep_dev_environment: bool = False):
        load_dotenv()
        if zep_dev_environment:
            self.zep = AsyncZep(
                api_key=os.getenv("ZEP_API_KEY"), 
                base_url="https://api.development.getzep.com/api/v2"
            )
        else:
            self.zep = AsyncZep(api_key=os.getenv("ZEP_API_KEY"))
        self.oai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
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

    async def download_dataset(self, file_path: str = "longmemeval_data.tar.gz"):
        """Download and extract the LongMemEval dataset"""
        file_id = "1zJgtYRFhOh5zDQzzatiddfjYhFSnyQ80"
        url = f"https://drive.google.com/uc?id={file_id}"

        if not os.path.exists(file_path):
            print(f"Downloading dataset to {file_path}...")
            gdown.download(url, file_path, quiet=False)
        else:
            print(f"'{file_path}' already exists, skipping download.")

        if not os.path.exists("./longmemeval_oracle.json"):
            print("Extracting dataset...")
            with tarfile.open(file_path, "r:gz") as tar:
                tar.extractall()
        else:
            print("'longmemeval_oracle.json' already exists, skipping extraction.")

    def load_dataset(
        self, dataset_option: str = "data/longmemeval_s.json"
    ) -> pd.DataFrame:
        """Load the LongMemEval dataset"""
        print(f"Loading dataset from {dataset_option}")
        return pd.read_json(dataset_option)

    async def ingest_data(
        self,
        df: pd.DataFrame,
        num_sessions: int = 500,
        question_type_filter: str = "single-session-assistant",
    ):
        """Ingest conversation data into Zep knowledge graph"""
        print(
            f"Ingesting {num_sessions} sessions with question type: {question_type_filter}"
        )

        for multi_session_idx in range(num_sessions):
            multi_session = df["haystack_sessions"].iloc[multi_session_idx]
            multi_session_dates = df["haystack_dates"].iloc[multi_session_idx]
            question_type = df["question_type"][multi_session_idx]

            if question_type != question_type_filter:
                continue

            print(f"Processing session {multi_session_idx}: {question_type}")

            user_id = f"lme_s_experiment_user_{multi_session_idx}"
            session_id = f"lme_s_experiment_session_{multi_session_idx}"

            try:
                await self.zep.user.add(user_id=user_id)
                await self.zep.memory.add_session(
                    user_id=user_id,
                    session_id=session_id,
                )

                for session_idx, session in enumerate(multi_session):
                    for msg in session:
                        date = multi_session_dates[session_idx] + " UTC"
                        date_format = "%Y/%m/%d (%a) %H:%M UTC"
                        date_string = datetime.strptime(date, date_format).replace(
                            tzinfo=timezone.utc
                        )

                        await self.zep.memory.add(
                            session_id=session_id,
                            messages=[
                                Message(
                                    role=msg["role"],
                                    role_type=msg["role"],
                                    content=msg["content"][:8000],
                                    created_at=date_string.isoformat(),
                                )
                            ],
                        )
            except Exception as e:
                print(f"Error processing session {multi_session_idx}: {e}")

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
            print(f"Processing batch starting at index {idx_start}")

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

    args = parser.parse_args()

    # Check if at least one action is specified
    if not args.ingest and not args.eval:
        parser.print_help()
        print("\nError: You must specify at least one action: --ingest or --eval")
        return

    runner = LongMemEvalRunner(zep_dev_environment=args.zep_dev_environment)

    # Download dataset
    if not args.skip_download:
        await runner.download_dataset()

    # Load dataset
    df = runner.load_dataset(args.dataset)

    # Ingest data
    if args.ingest and not args.baseline:
        await runner.ingest_data(df, args.num_sessions)

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

        print("\nEvaluation Results:")
        print(f"Total questions: {len(grades)}")
        print(f"Correct answers: {sum(grades)}")
        print(f"Accuracy: {accuracy:.3f}")
        print(f"Average duration: {avg_duration:.3f}s")
        if not args.baseline:
            print(f"Average retrieval duration: {avg_retrieval_duration:.3f}s")
        print(f"Results saved to: {args.output}")
    
    if args.ingest:
        print("Data ingestion completed successfully")


if __name__ == "__main__":
    asyncio.run(main())
