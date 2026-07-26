"""Formatting for Zep API failures.

Failures are reported as the API reported them. This package adds only the
operation that failed and passes the server's own status and message through
unchanged, so a failure reads the same here as it does from a direct SDK call.
"""

import httpx
from zep_cloud.core.api_error import ApiError

#: How a Zep API call can fail: a response the SDK rejected, or a transport
#: failure that never produced one (connection refused, read timeout, ...).
SubmitError = ApiError | httpx.TransportError


def format_api_error(operation: str, error: SubmitError) -> str:
    if isinstance(error, httpx.TransportError):
        # No response, so there is no status, body, or request id to report.
        return f"{operation} failed: transport error {type(error).__name__}"
    headers = error.headers or {}
    request_id = next(
        (
            str(value)
            for key, value in headers.items()
            if key.lower() in {"x-request-id", "request-id", "trace-id"}
        ),
        None,
    )
    parts = [f"status={error.status_code}"]
    if request_id:
        parts.append(f"request_id={request_id}")
    if error.body is not None:
        parts.append(f"body={error.body}")
    return f"{operation} failed: {', '.join(parts)}"
