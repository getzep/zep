import asyncio
import random

# 4xx statuses worth retrying — everything else in the 4xx range is a rejected
# request that will be rejected identically on every attempt.
RETRYABLE_CLIENT_ERRORS = {408, 409, 425, 429}


def is_retryable(exception) -> bool:
    """
    Whether an exception is worth retrying.

    Client errors (400 bad request, 404 not found, 422 unprocessable) are
    deterministic: retrying burns the full backoff schedule to arrive at the same
    rejection. Both the OpenAI and Zep SDKs expose `status_code` on their API
    errors, so one check covers both.
    """
    status = getattr(exception, "status_code", None)
    if isinstance(status, int) and 400 <= status < 500:
        return status in RETRYABLE_CLIENT_ERRORS
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
