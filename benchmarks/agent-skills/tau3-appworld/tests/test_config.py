from typing import Any

import pytest

import config


@pytest.mark.parametrize(
    ("api_url", "expected_url"),
    [
        ("https://api.example.com", "https://api.example.com/api/v4"),
        ("https://api.example.com/", "https://api.example.com/api/v4"),
        ("https://api.example.com/api/v4", "https://api.example.com/api/v4"),
        ("https://api.example.com/api/v4/", "https://api.example.com/api/v4"),
    ],
)
def test_zep_client_uses_api_v4_base_url(monkeypatch: Any, api_url: str, expected_url: str) -> None:
    captured: dict[str, Any] = {}
    http_client = object()

    def fake_zep(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(config, "Zep", fake_zep)
    monkeypatch.setattr(config, "zep_http_client", lambda: http_client)

    config.zep_client("api-key", api_url)

    assert captured == {
        "api_key": "api-key",
        "base_url": expected_url,
        "httpx_client": http_client,
    }


def test_load_config_reads_zep_and_openai_settings_from_environment(monkeypatch: Any) -> None:
    monkeypatch.setenv("ZEP_BASE_URL", "https://api.example.com/api/v4")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ZEP_API_KEY", "test-zep-key")

    loaded = config.load_config()

    assert loaded.api_url == "https://api.example.com/api/v4"
    assert loaded.openai_api_key == "test-openai-key"
    assert loaded.api_key == "test-zep-key"


def test_load_config_without_openai_key_for_snapshot_export(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ZEP_API_KEY", "test-zep-key")

    loaded = config.load_config(require_openai=False)

    assert loaded.api_url is None
    assert loaded.openai_api_key == ""
    assert loaded.api_key == "test-zep-key"
