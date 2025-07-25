#!/usr/bin/env python3
"""
LongMemEval Benchmark - Main orchestration script
"""

import asyncio
import argparse
import logging

from ingestion import IngestionRunner
from evaluation import EvaluationRunner
from common import load_dataset


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
    parser.add_argument(
        "--use-summarization",
        action="store_true",
        help="Use AI summarization for context composition (default: False)",
    )
    parser.add_argument(
        "--use-custom-ontology",
        action="store_true",
        help="Setup custom ontology for improved knowledge graph structure (default: False)",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="Replay failed users (delete and re-ingest) instead of normal ingestion (default: False)",
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

    # Initialize runners
    ingestion_runner = None
    evaluation_runner = None

    if args.ingest:
        ingestion_runner = IngestionRunner(
            zep_dev_environment=args.zep_dev_environment,
            log_level=args.log_level,
            use_custom_ontology=args.use_custom_ontology,
            replay_mode=args.replay,
        )

    if args.eval:
        evaluation_runner = EvaluationRunner(
            zep_dev_environment=args.zep_dev_environment,
            log_level=args.log_level,
            use_summarization=args.use_summarization,
        )

    # Download dataset if needed
    if not args.skip_download and ingestion_runner:
        await ingestion_runner.download_dataset()

    # Load dataset
    df = load_dataset(args.dataset)

    # Ingest data
    if args.ingest and not args.baseline:
        await ingestion_runner.ingest_data(
            df, args.num_sessions, args.question_type, args.start_index
        )

    # Run evaluation
    if args.eval:
        await evaluation_runner.run_evaluation(
            df, args.num_sessions, args.batch_size, args.baseline, args.output
        )


if __name__ == "__main__":
    asyncio.run(main())
