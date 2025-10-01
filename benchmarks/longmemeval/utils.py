#!/usr/bin/env python3
"""
Utility functions for LongMemEval benchmark
"""

import logging


def setup_logging(log_level: str, name: str | None = None) -> logging.Logger:
    """
    Configure logging with proper formatting

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        name: Logger name (defaults to __name__ if not provided)

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name or __name__)

    # Only configure if not already configured
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(getattr(logging, log_level.upper()))

    return logger
