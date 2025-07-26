#!/usr/bin/env python3
"""
Grid Search orchestration for LongMemEval benchmark
"""

import asyncio
import hashlib
import json
import logging
import yaml
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Dict, List, Any

from evaluation import EvaluationRunner
from calculate_scores import calculate_question_type_scores
from common import load_dataset


class GridSearchRunner:
    def __init__(
        self,
        config_file: str,
        zep_dev_environment: bool = False,
        log_level: str = "INFO",
    ):
        self.config_file = config_file
        self.zep_dev_environment = zep_dev_environment
        self.log_level = log_level
        self.logger = self._setup_logging()

        # Load configuration
        with open(config_file, "r") as f:
            self.config = yaml.safe_load(f)

        # Create unique folder for this grid search run
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_dir = Path(self.config["output"]["base_dir"])
        self.grid_search_dir = base_dir / f"grid_search_{timestamp}"
        self.grid_search_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup file logging for this grid search run
        self._setup_file_logging()

    def _setup_logging(self) -> logging.Logger:
        """Configure logging"""
        logger = logging.getLogger(__name__)
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)

        logger.setLevel(getattr(logging, self.log_level.upper()))
        return logger
    
    def _setup_file_logging(self):
        """Add file handler to capture all logs for this grid search run"""
        log_file = self.grid_search_dir / "grid_search.log"
        
        # Create file handler
        file_handler = logging.FileHandler(log_file)
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(logging.DEBUG)  # Capture all levels to file
        
        # Add file handler to root logger to capture all module logs
        root_logger = logging.getLogger()
        root_logger.addHandler(file_handler)
        
        # Also add to our specific logger
        self.logger.addHandler(file_handler)
        
        self.logger.info(f"Logging to file: {log_file}")

    def _generate_parameter_combinations(self) -> List[Dict[str, Any]]:
        """Generate all parameter combinations from config"""
        search_params = self.config["search_params"]
        models = self.config["models"]
        summarization = self.config["summarization"]
        evaluation_type = self.config["evaluation_type"]

        # Extract parameter lists
        param_lists = {
            "edge_limit": search_params["edge_limit"],
            "node_limit": search_params["node_limit"],
            "episode_limit": search_params["episode_limit"],
            "edge_reranker": search_params["edge_reranker"],
            "node_reranker": search_params["node_reranker"],
            "episode_reranker": search_params["episode_reranker"],
            "baseline": evaluation_type["baseline"],
            "use_summarization": summarization["use_summarization"],
            "response_model": models["response_model"],
            "grader_model": models["grader_model"],
            "summary_model": models["summary_model"],
        }

        # Generate all combinations
        param_names = list(param_lists.keys())
        param_values = list(param_lists.values())

        combinations = []
        for combo in product(*param_values):
            combination = dict(zip(param_names, combo))
            combinations.append(combination)

        return combinations

    def _create_config_hash(self, config: Dict[str, Any]) -> str:
        """Create a short hash from config for uniqueness"""
        config_str = json.dumps(config, sort_keys=True)
        return hashlib.md5(config_str.encode()).hexdigest()[:8]

    def _create_output_directory(self, config: Dict[str, Any]) -> Path:
        """Create unique output directory for this configuration"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        config_hash = self._create_config_hash(config)

        # Create descriptive folder name
        folder_parts = [timestamp]

        if self.config["output"].get("include_config_hash", True):
            folder_parts.append(f"hash_{config_hash}")

        # Add key parameters to folder name for easy identification
        key_params = [
            f"{'baseline' if config['baseline'] else 'zep'}",
            f"e{config['edge_limit']}n{config['node_limit']}ep{config['episode_limit']}",
            f"sum{1 if config['use_summarization'] else 0}",
            f"resp_{config['response_model'].replace('gpt-', '').replace('.', '_')}",
        ]
        folder_parts.extend(key_params)

        folder_name = "_".join(folder_parts)
        output_dir = self.grid_search_dir / folder_name
        output_dir.mkdir(exist_ok=True)

        return output_dir

    def _save_config_and_summary(
        self,
        output_dir: Path,
        config: Dict[str, Any],
        results_file: Path,
        summary_stats: Dict[str, Any],
    ):
        """Save configuration and evaluation summary"""
        # Save configuration
        config_file = output_dir / "config.json"
        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

        # Save evaluation summary with scores
        scores = calculate_question_type_scores(results_file)

        eval_summary = {
            "timestamp": datetime.now().isoformat(),
            "configuration": config,
            "results_file": str(results_file.name),
            "evaluation_stats": summary_stats,
            "question_type_scores": scores,
            "overall_accuracy": summary_stats.get("accuracy", 0),
            "total_questions": summary_stats.get("total_questions", 0),
            "correct_answers": summary_stats.get("correct_answers", 0),
            "avg_response_time": summary_stats.get("avg_response_time", 0),
            "avg_retrieval_time": summary_stats.get("avg_retrieval_time", 0),
        }

        summary_file = output_dir / "evaluation_summary.json"
        with open(summary_file, "w") as f:
            json.dump(eval_summary, f, indent=2)

        self.logger.info(f"Saved config and summary to {output_dir}")

    async def run_single_configuration(
        self, config: Dict[str, Any], df, num_sessions: int, batch_size: int
    ) -> Dict[str, Any]:
        """Run evaluation for a single parameter configuration"""
        output_dir = self._create_output_directory(config)
        results_file = output_dir / "results.jsonl"

        self.logger.info(f"Running configuration: {config}")
        self.logger.info(f"Output directory: {output_dir}")

        # Create evaluation runner with this configuration
        evaluation_runner = EvaluationRunner(
            zep_dev_environment=self.zep_dev_environment,
            log_level=self.log_level,
            config=config,
        )

        # Run evaluation
        start_time = datetime.now()
        
        # Run evaluation based on baseline setting
        results = []
        correct_count = 0
        total_duration = 0
        total_retrieval_duration = 0
        
        is_baseline = config.get('baseline', False)
        eval_type = "baseline" if is_baseline else "Zep"
        self.logger.info(f"Starting {eval_type} evaluation")
        self.logger.info(f"Processing {num_sessions} sessions in batches of {batch_size}")
        
        # Process in batches for efficiency
        for i in range(0, num_sessions, batch_size):
            batch_end = min(i + batch_size, num_sessions)
            batch_tasks = []
            
            # Create batch of evaluation tasks
            for j in range(i, batch_end):
                if is_baseline:
                    task = evaluation_runner.evaluate_conversation_baseline(df, j)
                else:
                    task = evaluation_runner.evaluate_conversation(df, j)
                batch_tasks.append(task)
            
            # Execute batch concurrently
            batch_results = await asyncio.gather(*batch_tasks)
            
            # Process results
            for result_data in batch_results:
                if is_baseline:
                    result, correct, duration = result_data
                    retrieval_duration = 0  # Baseline doesn't have retrieval
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
        with open(results_file, "w") as f:
            for result in results:
                f.write(json.dumps(result) + "\n")
        self.logger.info(f"Results saved to {results_file}")
        
        end_time = datetime.now()

        # Calculate summary statistics
        total_questions = len(results)
        summary_stats = {
            "evaluation_type": eval_type,
            "total_questions": total_questions,
            "correct_answers": correct_count,
            "accuracy": correct_count / total_questions if total_questions > 0 else 0,
            "avg_response_time": total_duration / total_questions
            if total_questions > 0
            else 0,
            "avg_retrieval_time": total_retrieval_duration / total_questions
            if total_questions > 0
            else 0,
            "total_runtime_seconds": (end_time - start_time).total_seconds(),
        }

        # Save configuration and summary
        self._save_config_and_summary(output_dir, config, results_file, summary_stats)

        return {
            "config": config,
            "output_dir": str(output_dir),
            "summary_stats": summary_stats,
            "config_hash": self._create_config_hash(config),
        }

    async def run_grid_search(
        self,
        dataset_file: str,
        num_sessions: int | None = None,
        batch_size: int | None = None,
    ):
        """Run grid search over all parameter combinations"""
        # Load dataset
        df = load_dataset(dataset_file)

        # Use config defaults if not specified
        num_sessions = num_sessions or self.config["evaluation"]["num_sessions"]
        batch_size = batch_size or self.config["evaluation"]["batch_size"]

        # Generate parameter combinations
        combinations = self._generate_parameter_combinations()
        self.logger.info("=== GRID SEARCH STARTING ===")
        self.logger.info(f"Grid search directory: {self.grid_search_dir}")
        self.logger.info(f"Generated {len(combinations)} parameter combinations to evaluate")
        self.logger.info(f"Each combination will process {num_sessions} sessions in batches of {batch_size}")
        self.logger.info(f"Total evaluations to run: {len(combinations) * num_sessions}")
        self.logger.info("=== GRID SEARCH STARTING ===")

        # Run evaluations
        all_results = []
        max_concurrent = self.config["execution"].get("max_concurrent_runs", 1)

        # Process combinations in batches to limit concurrency
        semaphore = asyncio.Semaphore(max_concurrent)

        async def run_with_semaphore(config):
            async with semaphore:
                return await self.run_single_configuration(
                    config, df, num_sessions, batch_size
                )

        # Create tasks for all combinations
        tasks = [run_with_semaphore(config) for config in combinations]

        # Execute with progress logging
        for i, task in enumerate(asyncio.as_completed(tasks)):
            result = await task
            all_results.append(result)
            self.logger.info(f"Completed {i + 1}/{len(combinations)} configurations")
            self.logger.info(f"Accuracy: {result['summary_stats']['accuracy']:.3f}")

        # Save overall grid search summary
        grid_summary = {
            "timestamp": datetime.now().isoformat(),
            "total_configurations": len(combinations),
            "dataset_file": dataset_file,
            "num_sessions": num_sessions,
            "batch_size": batch_size,
            "results": all_results,
        }

        summary_file = self.grid_search_dir / "grid_search_summary.json"
        with open(summary_file, "w") as f:
            json.dump(grid_summary, f, indent=2)

        self.logger.info(f"Grid search completed! Summary saved to {summary_file}")

        # Print top results
        sorted_results = sorted(
            all_results, key=lambda x: x["summary_stats"]["accuracy"], reverse=True
        )
        self.logger.info("Top 5 configurations by accuracy:")
        for i, result in enumerate(sorted_results[:5]):
            acc = result["summary_stats"]["accuracy"]
            config = result["config"]
            self.logger.info(f"{i + 1}. Accuracy: {acc:.3f} - {config}")

        return grid_summary


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run LongMemEval grid search")
    parser.add_argument(
        "--config",
        default="grid_search_config.yaml",
        help="Grid search configuration file",
    )
    parser.add_argument(
        "--dataset", default="data/longmemeval_s.json", help="Dataset file path"
    )
    parser.add_argument(
        "--num-sessions",
        type=int,
        help="Number of sessions to evaluate (overrides config)",
    )
    parser.add_argument(
        "--batch-size", type=int, help="Batch size for processing (overrides config)"
    )
    parser.add_argument(
        "--zep-dev-environment",
        action="store_true",
        help="Use Zep development environment",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level",
    )

    args = parser.parse_args()

    # Create and run grid search
    runner = GridSearchRunner(
        config_file=args.config,
        zep_dev_environment=args.zep_dev_environment,
        log_level=args.log_level,
    )

    await runner.run_grid_search(
        dataset_file=args.dataset,
        num_sessions=args.num_sessions,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    asyncio.run(main())
