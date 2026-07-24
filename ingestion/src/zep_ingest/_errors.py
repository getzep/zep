"""Privacy-safe formatting for API failures.

API response bodies may echo submitted content. Public result objects therefore
retain only the operation, HTTP status, and a server request identifier.
"""

from zep_cloud.core.api_error import ApiError


def safe_api_error(operation: str, error: ApiError) -> str:
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
