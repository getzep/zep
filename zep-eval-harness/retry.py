import asyncio
import random

# Statuses that mean "this exact request will never be accepted": retrying only
# burns the full backoff schedule to arrive at the same rejection. Kept
# deliberately narrow — everything else, including 404s for a resource that may
# not be visible yet, still retries exactly as it always did.
NON_RETRYABLE_STATUSES = {400, 422}


def is_retryable(exception) -> bool:
    """
    Whether an exception is worth retrying.

    Both the OpenAI and Zep SDKs expose `status_code` on their API errors, so one
    check covers both. Anything without a recognizable status is retried.
    """
    status = getattr(exception, "status_code", None)
    if isinstance(status, int) and status in NON_RETRYABLE_STATUSES:
        return False
    return True


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

    Non-retryable client errors (see is_retryable) are raised immediately.

    Args:
        fn: Async function to call
        max_retries: Maximum number of retry attempts (total attempts = max_retries + 1)
        initial_delay: Initial delay in seconds before first retry
        max_delay: Maximum delay cap in seconds
        description: Human-readable label for log messages

    Returns: Result of fn(*args, **kwargs)
    Raises: The last exception if all retries are exhausted, or immediately if
        the exception is not retryable
    """
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as e:
            last_exception = e
            if not is_retryable(e):
                print(f"  ✗ {description} rejected (not retryable): {e}")
                raise
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
