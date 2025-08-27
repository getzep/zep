"""Basic tests for zep-livekit integration."""

import pytest


def test_package_imports():
    """Test that the package imports correctly."""
    try:
        import zep_livekit

        assert hasattr(zep_livekit, "__version__")
    except ImportError:
        pytest.fail("Failed to import zep_livekit package")


def test_agent_imports():
    """Test that agent classes can be imported."""
    try:
        from zep_livekit import ZepGraphAgent, ZepUserAgent

        assert ZepUserAgent is not None
        assert ZepGraphAgent is not None
    except ImportError:
        pytest.fail("Failed to import agent classes")


def test_exception_imports():
    """Test that custom exceptions can be imported."""
    try:
        from zep_livekit.exceptions import AgentConfigurationError

        assert AgentConfigurationError is not None
    except ImportError:
        pytest.fail("Failed to import exception classes")


def test_version_format():
    """Test that version follows semantic versioning."""
    import zep_livekit

    version = zep_livekit.__version__

    # Basic version format check (major.minor.patch)
    parts = version.split(".")
    assert len(parts) >= 2, f"Version should have at least 2 parts, got: {version}"

    # Check that major and minor are numeric
    assert parts[0].isdigit(), f"Major version should be numeric, got: {parts[0]}"
    assert parts[1].isdigit(), f"Minor version should be numeric, got: {parts[1]}"
