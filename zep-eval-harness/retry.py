import asyncio
import random


async def retry_with_backoff(
    fn,
    *args,
    max_retries=8,
    initial_delay=2.0,
    max_delay=300.0,
    description="operation",
    **kwargs,
):
    """
    Retry an async callable with exponential backoff and jitter.

    Args:
        fn: Async function to call
        max_retries: Maximum number of retry attempts (total attempts = max_retries + 1)
        initial_delay: Initial delay in seconds before first retry
        max_delay: Maximum delay cap in seconds
        description: Human-readable label for log messages

    Returns: Result of fn(*args, **kwargs)
    Raises: The last exception if all retries are exhausted
    """
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as e:
            last_exception = e
            if attempt == max_retries:
                break
            delay = min(initial_delay * (2 ** attempt), max_delay)
            jitter = random.uniform(0, delay * 0.1)
            delay += jitter
            print(
                f"  ⚠ {description} failed (attempt {attempt + 1}/{max_retries + 1}): {e}"
            )
            print(f"    Retrying in {delay:.1f}s...")
            await asyncio.sleep(delay)

    print(f"  ✗ {description} failed after {max_retries + 1} attempts")
    raise last_exception
