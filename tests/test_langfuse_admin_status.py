from __future__ import annotations

from gateway_core.runtime.admin.endpoints import route_langfuse_status, route_phoenix_status


def test_route_langfuse_status_returns_configuration(monkeypatch) -> None:
    monkeypatch.setattr("gateway_core.runtime.admin.endpoints._require_gateway_auth", lambda authorization: "token")
    monkeypatch.setenv("LANGFUSE_ENABLED", "1")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://langfuse.local")

    status = route_langfuse_status()

    assert status["enabled"] is True
    assert status["configured"] is True
    assert status["dashboard_url"] == "http://langfuse.local"


def test_route_phoenix_status_returns_configuration(monkeypatch) -> None:
    monkeypatch.setattr("gateway_core.runtime.admin.endpoints._require_gateway_auth", lambda authorization: "token")
    monkeypatch.setenv("PHOENIX_ENABLED", "1")
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://phoenix.local/v1/traces")

    status = route_phoenix_status()

    assert status["enabled"] is True
    assert status["configured"] is True
    assert status["dashboard_url"] == "http://phoenix.local"
