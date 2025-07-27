#!/usr/bin/env python3
"""
Bayesian optimization for LongMemEval benchmark using Optuna
"""

import asyncio
import json
import logging
import optuna
import random
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from evaluation import EvaluationRunner
from calculate_scores import calculate_question_type_scores
from common import load_dataset


class BayesianSearchRunner:
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

        # Extract Bayesian optimization settings
        bayesian_config = self.config["search_method"]["bayesian"]
        self.n_trials = bayesian_config["n_trials"]
        self.timeout = bayesian_config.get("timeout")
        self.seed = bayesian_config.get("seed", 42)

        # Set random seeds for reproducibility
        random.seed(self.seed)

        # Create unique folder for this search run
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_dir = (
            Path(self.config["output"]["base_dir"]).parent / "bayesian_search_results"
        )
        self.search_dir = base_dir / f"bayesian_search_{timestamp}"
        self.search_dir.mkdir(parents=True, exist_ok=True)

        # Setup file logging
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
        """Add file handler to capture all logs for this search run"""
        log_file = self.search_dir / "bayesian_search.log"

        # Create file handler
        file_handler = logging.FileHandler(log_file)
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(logging.DEBUG)

        # Add to root logger to capture all module logs
        root_logger = logging.getLogger()
        root_logger.addHandler(file_handler)

        # Also add to our specific logger
        self.logger.addHandler(file_handler)

        self.logger.info(f"Logging to file: {log_file}")

    def _suggest_parameters(self, trial: optuna.Trial) -> Dict[str, Any]:
        """Suggest parameters with domain knowledge constraints using config ranges"""

        search_params = self.config["search_params"]
        models = self.config["models"]
        summarization = self.config["summarization"]
        evaluation_type = self.config["evaluation_type"]

        # Search parameters - suggest from config ranges
        edge_limit = trial.suggest_categorical(
            "edge_limit", search_params["edge_limit"]
        )
        node_limit = trial.suggest_categorical(
            "node_limit", search_params["node_limit"]
        )
        episode_limit = trial.suggest_categorical(
            "episode_limit", search_params["episode_limit"]
        )

        # Reranker options - suggest from config choices
        edge_reranker = trial.suggest_categorical(
            "edge_reranker", search_params["edge_reranker"]
        )
        node_reranker = trial.suggest_categorical(
            "node_reranker", search_params["node_reranker"]
        )

        # Domain constraint: Only include episode reranker if we're actually searching episodes
        if episode_limit > 0:
            episode_reranker = trial.suggest_categorical(
                "episode_reranker", search_params["episode_reranker"]
            )
        else:
            episode_reranker = None  # No point reranking zero episodes

        # Other parameters from config
        baseline = trial.suggest_categorical("baseline", evaluation_type["baseline"])
        strategy = trial.suggest_categorical("strategy", summarization["strategy"])

        # Model configurations (typically fixed, but could vary)
        response_model = trial.suggest_categorical(
            "response_model", models["response_model"]
        )
        grader_model = trial.suggest_categorical("grader_model", models["grader_model"])
        summary_model = trial.suggest_categorical(
            "summary_model", models["summary_model"]
        )

        # Build configuration
        config = {
            "edge_limit": edge_limit,
            "node_limit": node_limit,
            "episode_limit": episode_limit,
            "edge_reranker": edge_reranker,
            "node_reranker": node_reranker,
            "episode_reranker": episode_reranker,
            "baseline": baseline,
            "strategy": strategy,
            "response_model": response_model,
            "grader_model": grader_model,
            "summary_model": summary_model,
        }

        return config

    def _create_config_hash(self, config: Dict[str, Any]) -> str:
        """Create a short hash from config for uniqueness"""
        import hashlib

        config_str = json.dumps(config, sort_keys=True)
        return hashlib.md5(config_str.encode()).hexdigest()[:8]

    def _create_output_directory(
        self, trial_number: int, config: Dict[str, Any]
    ) -> Path:
        """Create unique output directory for this trial"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        config_hash = self._create_config_hash(config)

        # Create descriptive folder name
        folder_parts = [
            f"trial_{trial_number:03d}",
            timestamp,
            f"hash_{config_hash}",
            f"{'baseline' if config['baseline'] else 'zep'}",
            f"e{config['edge_limit']}n{config['node_limit']}ep{config['episode_limit']}",
            f"sum_{config['strategy'] or 'none'}",
        ]

        folder_name = "_".join(folder_parts)
        output_dir = self.search_dir / folder_name
        output_dir.mkdir(exist_ok=True)

        return output_dir

    def _save_trial_results(
        self,
        output_dir: Path,
        trial_number: int,
        config: Dict[str, Any],
        results_file: Path,
        summary_stats: Dict[str, Any],
        accuracy: float,
    ):
        """Save trial configuration and results"""
        # Save configuration
        config_file = output_dir / "config.json"
        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

        # Calculate question type scores
        scores = calculate_question_type_scores(results_file)

        # Save trial summary
        trial_summary = {
            "trial_number": trial_number,
            "timestamp": datetime.now().isoformat(),
            "configuration": config,
            "results_file": str(results_file.name),
            "evaluation_stats": summary_stats,
            "question_type_scores": scores,
            "accuracy": accuracy,
            "total_questions": summary_stats.get("total_questions", 0),
            "correct_answers": summary_stats.get("correct_answers", 0),
            "avg_response_time": summary_stats.get("avg_response_time", 0),
            "avg_retrieval_time": summary_stats.get("avg_retrieval_time", 0),
        }

        summary_file = output_dir / "trial_summary.json"
        with open(summary_file, "w") as f:
            json.dump(trial_summary, f, indent=2)

        self.logger.info(f"Saved trial {trial_number} results to {output_dir}")

    def _get_random_session_indices(
        self, total_sessions: int, num_sessions: int
    ) -> List[int]:
        """Get random session indices for sampling"""
        if num_sessions >= total_sessions:
            # Use all sessions if requesting more than available
            return list(range(total_sessions))
        else:
            # Randomly sample without replacement
            return sorted(random.sample(range(total_sessions), num_sessions))

    async def _objective(
        self, trial: optuna.Trial, df, num_sessions: int, batch_size: int
    ) -> float:
        """Objective function for Optuna optimization"""

        # Get suggested parameters with domain constraints
        config = self._suggest_parameters(trial)

        # Create output directory for this trial
        output_dir = self._create_output_directory(trial.number, config)
        results_file = output_dir / "results.jsonl"

        self.logger.info(f"Trial {trial.number}: {config}")
        self.logger.info(f"Output directory: {output_dir}")

        # Create evaluation runner
        evaluation_runner = EvaluationRunner(
            zep_dev_environment=self.zep_dev_environment,
            log_level=self.log_level,
            config=config,
        )

        # Get random session indices for sampling
        total_sessions = len(df)
        session_indices = self._get_random_session_indices(total_sessions, num_sessions)
        actual_num_sessions = len(session_indices)

        self.logger.info(
            f"Trial {trial.number}: Randomly sampling {actual_num_sessions} sessions from {total_sessions} total"
        )

        # Run evaluation
        start_time = datetime.now()

        results = []
        correct_count = 0
        total_duration = 0
        total_retrieval_duration = 0

        is_baseline = config.get("baseline", False)
        eval_type = "baseline" if is_baseline else "Zep"
        self.logger.info(f"Trial {trial.number}: Starting {eval_type} evaluation")

        # Process in batches using random session indices
        for i in range(0, actual_num_sessions, batch_size):
            batch_end = min(i + batch_size, actual_num_sessions)
            batch_tasks = []

            # Create batch of evaluation tasks using random indices
            for idx in range(i, batch_end):
                session_idx = session_indices[idx]  # Use random session index
                if is_baseline:
                    task = evaluation_runner.evaluate_conversation_baseline(
                        df, session_idx
                    )
                else:
                    task = evaluation_runner.evaluate_conversation(df, session_idx)
                batch_tasks.append(task)

            # Execute batch concurrently
            batch_results = await asyncio.gather(*batch_tasks)

            # Process results
            for result_data in batch_results:
                if is_baseline:
                    result, correct, duration = result_data
                    retrieval_duration = 0
                else:
                    result, correct, duration, retrieval_duration = result_data

                results.append(result)
                correct_count += correct
                total_duration += duration
                total_retrieval_duration += retrieval_duration

            # Report intermediate progress to Optuna for early stopping
            intermediate_accuracy = correct_count / len(results) if results else 0
            trial.report(intermediate_accuracy, i // batch_size)

            # Check if trial should be pruned
            if trial.should_prune():
                self.logger.info(
                    f"Trial {trial.number}: Pruned at batch {i // batch_size + 1}"
                )
                raise optuna.TrialPruned()

        # Save results
        with open(results_file, "w") as f:
            for result in results:
                f.write(json.dumps(result) + "\n")

        end_time = datetime.now()

        # Calculate final metrics
        total_questions = len(results)
        accuracy = correct_count / total_questions if total_questions > 0 else 0

        summary_stats = {
            "evaluation_type": eval_type,
            "total_questions": total_questions,
            "correct_answers": correct_count,
            "accuracy": accuracy,
            "avg_response_time": total_duration / total_questions
            if total_questions > 0
            else 0,
            "avg_retrieval_time": total_retrieval_duration / total_questions
            if total_questions > 0
            else 0,
            "total_runtime_seconds": (end_time - start_time).total_seconds(),
        }

        # Save trial results
        self._save_trial_results(
            output_dir, trial.number, config, results_file, summary_stats, accuracy
        )

        self.logger.info(f"Trial {trial.number}: Accuracy = {accuracy:.4f}")

        return accuracy  # Optuna will maximize this

    async def run_bayesian_search(
        self,
        dataset_file: str,
        num_sessions: int | None = None,
        batch_size: int | None = None,
    ):
        """Run Bayesian optimization search"""

        # Load dataset
        df = load_dataset(dataset_file)

        # Use config defaults if not specified
        num_sessions = (
            num_sessions
            if num_sessions is not None
            else self.config["evaluation"]["num_sessions"]
        )
        batch_size = (
            batch_size
            if batch_size is not None
            else self.config["evaluation"]["batch_size"]
        )

        self.logger.info("=== BAYESIAN SEARCH STARTING ===")
        self.logger.info(f"Search directory: {self.search_dir}")
        self.logger.info(f"Number of trials: {self.n_trials}")
        self.logger.info(f"Sessions per trial: {num_sessions}")
        self.logger.info(f"Batch size: {batch_size}")
        self.logger.info(f"Total dataset sessions: {len(df)}")
        self.logger.info("=== BAYESIAN SEARCH STARTING ===")

        # Create Optuna study with config settings
        bayesian_config = self.config["search_method"]["bayesian"]

        # Configure pruner
        pruner_config = bayesian_config.get("pruner_config", {})
        if bayesian_config.get("pruner") == "MedianPruner":
            pruner = optuna.pruners.MedianPruner(
                n_startup_trials=pruner_config.get("n_startup_trials", 10),
                n_warmup_steps=pruner_config.get("n_warmup_steps", 5),
            )
        else:
            pruner = optuna.pruners.NopPruner()

        # Configure sampler
        sampler_type = bayesian_config.get("sampler", "GP")
        if sampler_type == "GP":
            sampler = optuna.samplers.GPSampler(seed=self.seed)
        elif sampler_type == "TPE":
            sampler = optuna.samplers.TPESampler(seed=self.seed)
        else:
            sampler = optuna.samplers.RandomSampler(seed=self.seed)

        study = optuna.create_study(
            direction="maximize",  # Maximize accuracy
            pruner=pruner,
            sampler=sampler,
        )

        # Define objective wrapper
        async def objective_wrapper(trial):
            return await self._objective(trial, df, num_sessions, batch_size)

        # Run optimization
        best_accuracy = 0
        best_params = None

        for trial_num in range(self.n_trials):
            trial = study.ask()
            try:
                accuracy = await objective_wrapper(trial)
                study.tell(trial, accuracy)

                if accuracy > best_accuracy:
                    best_accuracy = accuracy
                    best_params = trial.params
                    self.logger.info(
                        f"New best accuracy: {best_accuracy:.4f} with params: {best_params}"
                    )

            except optuna.TrialPruned:
                study.tell(trial, state=optuna.trial.TrialState.PRUNED)
                continue
            except Exception as e:
                self.logger.error(f"Trial {trial.number} failed: {e}")
                study.tell(trial, state=optuna.trial.TrialState.FAIL)
                continue

        # Save optimization results
        search_summary = {
            "timestamp": datetime.now().isoformat(),
            "n_trials": self.n_trials,
            "dataset_file": dataset_file,
            "num_sessions": num_sessions,
            "batch_size": batch_size,
            "best_accuracy": best_accuracy,
            "best_params": best_params,
            "study_statistics": {
                "n_trials": len(study.trials),
                "n_complete_trials": len(
                    [
                        t
                        for t in study.trials
                        if t.state == optuna.trial.TrialState.COMPLETE
                    ]
                ),
                "n_pruned_trials": len(
                    [
                        t
                        for t in study.trials
                        if t.state == optuna.trial.TrialState.PRUNED
                    ]
                ),
                "n_failed_trials": len(
                    [t for t in study.trials if t.state == optuna.trial.TrialState.FAIL]
                ),
            },
        }

        summary_file = self.search_dir / "bayesian_search_summary.json"
        with open(summary_file, "w") as f:
            json.dump(search_summary, f, indent=2)

        self.logger.info(f"Bayesian search completed! Summary saved to {summary_file}")
        self.logger.info(f"Best accuracy: {best_accuracy:.4f}")
        self.logger.info(f"Best parameters: {best_params}")

        # Save detailed study results
        trials_data = []
        for trial in study.trials:
            trial_data = {
                "number": trial.number,
                "value": trial.value,
                "params": trial.params,
                "state": trial.state.name,
            }
            trials_data.append(trial_data)

        trials_file = self.search_dir / "all_trials.json"
        with open(trials_file, "w") as f:
            json.dump(trials_data, f, indent=2)

        return search_summary


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run LongMemEval Bayesian search")
    parser.add_argument(
        "--config",
        default="search_config.yaml",
        help="Configuration file (default: search_config.yaml)",
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

    # Create and run Bayesian search
    runner = BayesianSearchRunner(
        config_file=args.config,
        zep_dev_environment=args.zep_dev_environment,
        log_level=args.log_level,
    )

    await runner.run_bayesian_search(
        dataset_file=args.dataset,
        num_sessions=args.num_sessions,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    asyncio.run(main())
