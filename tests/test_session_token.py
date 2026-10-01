"""The desktop engine's session-token gate.

In local mode the engine must answer only its own shell. These tests drive
the ASGI app directly (no HTTP client needed) with the token switched on.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import api.server as server

TOKEN = "s3cret-token"


def call(method: str, path: str, headers: dict[str, str] | None = None, query: str = ""):
    """One request through the ASGI app → (status, parsed-or-raw body)."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
    }
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(server.app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    try:
        return status, json.loads(body)
    except ValueError:
        return status, body


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(server, "_SESSION_TOKEN", TOKEN)
    return TOKEN


def test_hosted_mode_needs_no_token():
    status, body = call("GET", "/api/cat62_reference")
    assert status == 200
    assert isinstance(body, dict)


def test_health_stays_open_and_reports_version(token):
    status, body = call("GET", "/api/health")
    assert status == 200
    assert body["ok"] is True
    assert body["version"] == server.__version__


def test_request_without_token_is_rejected(token):
    status, body = call("GET", "/api/cat62_reference")
    assert status == 401
    assert body == {"detail": "Unauthorized"}


def test_wrong_token_is_rejected(token):
    status, _ = call("GET", "/api/cat62_reference", {"Authorization": "Bearer nope"})
    assert status == 401


def test_bearer_token_is_accepted(token):
    status, _ = call("GET", "/api/cat62_reference", {"Authorization": f"Bearer {TOKEN}"})
    assert status == 200


def test_query_token_is_accepted_on_get_only(token):
    status, _ = call("GET", "/api/cat62_reference", query=f"t={TOKEN}")
    assert status == 200
    # A POST must use the header: a token in a URL ends up in logs and history.
    status, _ = call("POST", "/api/generate_batch", query=f"t={TOKEN}")
    assert status == 401


def test_preflight_passes_without_token(token):
    # Browsers never send credentials on a CORS preflight.
    status, _ = call(
        "OPTIONS",
        "/api/generate_batch",
        {
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert status == 200


def test_unauthorized_response_still_has_cors_headers(token):
    scope_headers = {"Origin": "http://localhost:3000"}
    status, _ = call("GET", "/api/cat62_reference", scope_headers)
    assert status == 401
