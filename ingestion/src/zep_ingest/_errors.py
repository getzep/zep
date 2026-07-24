"""Privacy-safe formatting for API failures.

API response bodies may echo submitted content. Public result objects therefore
retain only the operation, HTTP status, and a server request identifier.
"""

import httpx
from zep_cloud.core.api_error import ApiError

#: How a Zep API call can fail: a response the SDK rejected, or a transport
#: failure that never produced one (connection refused, read timeout, ...).
SubmitError = ApiError | httpx.TransportError


def safe_api_error(operation: str, error: SubmitError) -> str:
    if isinstance(error, httpx.TransportError):
        # No response, so no status or request id; the exception type is the
        # only detail, and it never echoes submitted content.
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
    suffix = f", request_id={request_id}" if request_id else ""
    return f"{operation} failed: status={error.status_code}{suffix}"
