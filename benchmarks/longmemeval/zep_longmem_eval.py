#!/usr/bin/env python3
"""
LongMemEval Benchmark - Main orchestration script
"""

import asyncio
import argparse
import logging
import yaml

from ingestion import IngestionRunner
from grid_search import GridSearchRunner
from bayesian_search import BayesianSearchRunner
from common import load_dataset


async def main():
    parser = argparse.ArgumentParser(description="Run LongMemEval evaluation")
    parser.add_argument(
        "--dataset",
        default="data/longmemeval_s.json",
        help="Dataset file path (default: data/longmemeval_s.json)",
    )
    parser.add_argument("--ingest", action="store_true", help="Run data ingestion")
    parser.add_argument("--eval", action="store_true", help="Run evaluation")
    parser.add_argument(
        "--skip-download", action="store_true", help="Skip dataset download"
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
        "--use-custom-ontology",
        action="store_true",
        help="Setup custom ontology for improved knowledge graph structure (default: False)",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="Replay failed users (delete and re-ingest) instead of normal ingestion (default: False)",
    )
    parser.add_argument(
        "--config",
        default="search_config.yaml",
        help="Search configuration file (default: search_config.yaml)",
    )
    parser.add_argument(
        "--num-sessions",
        type=int,
        default=500,
        help="Number of sessions to evaluate per trial/config (default: 500)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=15,
        help="Batch size for processing (default: 15)",
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

    # Initialize ingestion runner if needed
    ingestion_runner = None
    if args.ingest:
        ingestion_runner = IngestionRunner(
            zep_dev_environment=args.zep_dev_environment,
            log_level=args.log_level,
            use_custom_ontology=args.use_custom_ontology,
            replay_mode=args.replay,
        )

    # Download dataset if needed
    if not args.skip_download and ingestion_runner:
        await ingestion_runner.download_dataset()

    # Load dataset
    df = load_dataset(args.dataset)

    # Ingest data
    if args.ingest and ingestion_runner:
        await ingestion_runner.ingest_data(
            df, 500, args.question_type, args.start_index  # Use default session count for ingestion
        )

    # Run evaluation
    if args.eval:
        # Read config file to determine search method
        with open(args.config, "r") as f:
            config = yaml.safe_load(f)
        
        search_method = config["search_method"]["type"]
        logger.info(f"Search method from config: {search_method}")
        
        if search_method == "grid":
            logger.info("Running grid search evaluation")
            grid_runner = GridSearchRunner(
                config_file=args.config,
                zep_dev_environment=args.zep_dev_environment,
                log_level=args.log_level,
            )
            await grid_runner.run_grid_search(
                dataset_file=args.dataset,
                num_sessions=args.num_sessions if args.num_sessions != 500 else None,  # Use config values if default
                batch_size=args.batch_size if args.batch_size != 15 else None,      # Use config values if default
            )
        elif search_method == "bayesian":
            logger.info("Running Bayesian optimization evaluation")
            bayesian_runner = BayesianSearchRunner(
                config_file=args.config,
                zep_dev_environment=args.zep_dev_environment,
                log_level=args.log_level,
            )
            await bayesian_runner.run_bayesian_search(
                dataset_file=args.dataset,
                num_sessions=args.num_sessions if args.num_sessions != 500 else None,  # Use config values if default
                batch_size=args.batch_size if args.batch_size != 15 else None,      # Use config values if default
            )
        else:
            logger.error(f"Unknown search method '{search_method}' in config. Must be 'grid' or 'bayesian'")
            return


if __name__ == "__main__":
    asyncio.run(main())
