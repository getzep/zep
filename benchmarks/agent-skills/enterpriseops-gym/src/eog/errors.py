class InfrastructureError(RuntimeError):
    """The benchmark infrastructure failed before task scoring."""


class RetryExhaustedError(InfrastructureError):
    """A retryable provider error remained after all attempts."""


class WallClockLimitError(RetryExhaustedError):
    """The episode wall-clock limit left less than one second for a request."""

    reason = "wall_clock_limit"


class PolicyFlagError(RuntimeError):
    """A provider rejected a request because of an input policy flag."""


class NonRetryableProviderError(RuntimeError):
    """A provider returned an error that must stop the run."""


class ContextOverflowError(NonRetryableProviderError):
    """The prompt exceeded the model context length."""


class BudgetExceeded(RuntimeError):
    """The configured model-call budget has been reached."""


class AbortRequestedError(RuntimeError):
    """A second stop signal requested an episode abort."""
