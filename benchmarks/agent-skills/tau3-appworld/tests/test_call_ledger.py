import httpx
import pytest

from call_ledger import CURRENT_LEDGER, CallLedger, LedgerTransport, error_detail


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=LedgerTransport("zep", httpx.MockTransport(handler)))


def test_ledger_counts_each_failed_attempt_by_operation_and_status() -> None:
    statuses = iter([429, 503, 200])
    client = _client(lambda request: httpx.Response(next(statuses)))
    ledger = CallLedger()
    token = CURRENT_LEDGER.set(ledger)
    try:
        for _ in range(3):
            client.post("http://zep/api/v4/agents/agent-demo/traces/trace-demo")
    finally:
        CURRENT_LEDGER.reset(token)
    assert ledger.rows() == [
        {
            "service": "zep",
            "operation": "POST /api/v4/agents/{id}/traces/{id}",
            "outcome": "429",
            "count": 1,
        },
        {
            "service": "zep",
            "operation": "POST /api/v4/agents/{id}/traces/{id}",
            "outcome": "503",
            "count": 1,
        },
    ]


def test_ledger_counts_a_transport_error_and_raises_it() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    ledger = CallLedger()
    token = CURRENT_LEDGER.set(ledger)
    try:
        with pytest.raises(httpx.ConnectError):
            _client(fail).get("http://zep/api/v4/agents")
    finally:
        CURRENT_LEDGER.reset(token)
    assert ledger.rows() == [
        {"service": "zep", "operation": "GET /api/v4/agents", "outcome": "ConnectError", "count": 1}
    ]


def test_calls_outside_an_episode_are_not_counted() -> None:
    response = _client(lambda request: httpx.Response(500)).get("http://zep/healthz")
    assert response.status_code == 500
    assert CURRENT_LEDGER.get() is None


def test_error_detail_names_the_class_and_status() -> None:
    class StatusError(Exception):
        status_code = 429

    assert error_detail(StatusError("slow down")) == "StatusError:429"
    assert error_detail(ValueError("empty")) == "ValueError: empty"


def test_zep_client_keeps_the_sdk_timeout() -> None:
    from zep_cloud import Zep

    from call_ledger import ZEP_TIMEOUT_S, zep_http_client

    http = zep_http_client()
    client = Zep(api_key="k", base_url="http://zep/api/v4", httpx_client=http)
    assert http.timeout.read == ZEP_TIMEOUT_S == 60.0
    # With no SDK timeout, the SDK sends each request with the httpx client timeout.
    assert client._client_wrapper.get_timeout() is None
    assert http.timeout == httpx.Timeout(60.0)


def test_provider_clients_keep_their_sdk_timeouts() -> None:
    from call_ledger import anthropic_http_client, openai_http_client

    assert openai_http_client("model").timeout.read == 600.0
    assert anthropic_http_client("anthropic").timeout.read == 600.0


def test_ledger_counts_a_body_read_failure() -> None:
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"{"
            raise httpx.ReadError("connection reset")

    client = _client(lambda request: httpx.Response(200, stream=BrokenStream()))
    ledger = CallLedger()
    token = CURRENT_LEDGER.set(ledger)
    try:
        with pytest.raises(httpx.ReadError):
            client.get("http://zep/api/v4/agents")
    finally:
        CURRENT_LEDGER.reset(token)
    assert ledger.rows() == [
        {"service": "zep", "operation": "GET /api/v4/agents", "outcome": "ReadError", "count": 1}
    ]


def test_error_detail_stores_no_headers_body_or_raw_values() -> None:
    from zep_cloud.core.api_error import ApiError

    exc = ApiError(
        status_code=429,
        headers={"authorization": "Bearer secret"},
        body={"email": "jane@example.com"},
    )
    assert error_detail(exc) == "ApiError:429"
    request = httpx.Request("GET", "http://zep/x")
    status_error = httpx.HTTPStatusError(
        "bad", request=request, response=httpx.Response(503, request=request)
    )
    assert error_detail(status_error) == "HTTPStatusError:503"
    detail = error_detail(ValueError("mail jane.doe@example.com now\nsecond line"))
    assert "jane.doe@example.com" not in detail
    assert "second line" not in detail
    assert detail.startswith("ValueError: mail ")


def test_operation_name_keeps_only_route_words() -> None:
    from call_ledger import operation_name

    path = "/api/v4/agents/agent-demo/traces/trace-demo/events"
    assert (
        operation_name(httpx.Request("POST", f"http://zep{path}"))
        == "POST /api/v4/agents/{id}/traces/{id}/events"
    )
    assert operation_name(
        httpx.Request("GET", "http://zep/api/v4/agents/student-skills/john_smith")
    ) == ("GET /api/v4/agents/{id}/{id}")
    assert operation_name(httpx.Request("POST", "http://appworld/initialize")) == "POST /initialize"
