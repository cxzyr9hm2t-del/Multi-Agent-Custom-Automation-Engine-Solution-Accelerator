"""Tests for the ask_user MCP bridge.

The tool used to hold one HTTP request open for the whole time a human took to
answer. It now creates the clarification and polls for the result, so no request
waits on a person. That moved the risk into a loop with a deadline and several
terminal statuses, none of which had any coverage before — this file is that
coverage.
"""

import asyncio

import pytest

pytest.importorskip("fastmcp", reason="fastmcp not installed in backend venv; run with mcp_server venv")

import services.ask_user_service as aus  # noqa: E402
from services.ask_user_service import AskUserService  # noqa: E402


class _Response:
    """Minimal httpx.Response stand-in."""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _Client:
    """Records POSTs and replays a scripted sequence of responses."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        self.calls.append((url, json))
        if not self._script:
            raise AssertionError(f"unscripted POST to {url}")
        nxt = self._script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return _Response(nxt)


@pytest.fixture
def ask_user(monkeypatch):
    """Return the registered ask_user callable, with sleeping made instant."""
    captured = {}

    class _MCP:
        def tool(self, *args, **kwargs):
            def decorator(fn):
                captured["fn"] = fn
                return fn
            return decorator

    AskUserService().register_tools(_MCP())

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(aus.asyncio, "sleep", _no_sleep)
    return captured["fn"]


def _install(monkeypatch, script):
    client = _Client(script)
    monkeypatch.setattr(aus.httpx, "AsyncClient", lambda **kw: client)
    return client


class TestAskUserPolling:
    @pytest.mark.asyncio
    async def test_returns_the_answer_once_it_completes(self, ask_user, monkeypatch):
        client = _install(monkeypatch, [
            {"request_id": "r-1", "status": "input_required", "poll_interval_seconds": 0.01},
            {"status": "input_required"},
            {"status": "completed", "answer": "Tuesday"},
        ])
        assert await ask_user("when?", "tok") == "Tuesday"

        # Created once, then polled — and the token travels on every call, since
        # the poll is authorized exactly as the ask is.
        assert client.calls[0][0].endswith("/clarification/ask")
        assert all(c[1]["session_token"] == "tok" for c in client.calls)
        assert all(c[0].endswith("/clarification/result") for c in client.calls[1:])

    @pytest.mark.asyncio
    async def test_no_single_request_waits_for_the_human(self, ask_user, monkeypatch):
        """The point of the change: per-request timeout is short, not 300s."""
        seen = {}
        real = _Client([
            {"request_id": "r-1"},
            {"status": "completed", "answer": "ok"},
        ])

        def _factory(**kwargs):
            seen.update(kwargs)
            return real

        monkeypatch.setattr(aus.httpx, "AsyncClient", _factory)
        await ask_user("q", "tok")
        assert seen["timeout"] == aus.REQUEST_TIMEOUT
        assert seen["timeout"] < aus.ASK_USER_TIMEOUT

    @pytest.mark.asyncio
    async def test_expired_tells_the_agent_to_proceed(self, ask_user, monkeypatch):
        _install(monkeypatch, [
            {"request_id": "r-1"},
            {"status": "expired"},
        ])
        assert "did not respond in time" in await ask_user("q", "tok")

    @pytest.mark.asyncio
    async def test_unknown_tells_the_agent_to_proceed(self, ask_user, monkeypatch):
        _install(monkeypatch, [
            {"request_id": "r-1"},
            {"status": "unknown"},
        ])
        assert "did not respond in time" in await ask_user("q", "tok")

    @pytest.mark.asyncio
    async def test_an_empty_answer_is_reported_not_returned_blank(self, ask_user, monkeypatch):
        _install(monkeypatch, [
            {"request_id": "r-1"},
            {"status": "completed", "answer": ""},
        ])
        assert await ask_user("q", "tok") == "The user did not provide an answer."

    @pytest.mark.asyncio
    async def test_the_deadline_stops_the_loop(self, ask_user, monkeypatch):
        """Without this the poll loop would run forever against a stuck backend."""
        monkeypatch.setattr(aus, "ASK_USER_TIMEOUT", 0.0)
        _install(monkeypatch, [{"request_id": "r-1"}])
        assert "did not respond in time" in await ask_user("q", "tok")

    @pytest.mark.asyncio
    async def test_the_backend_can_retune_the_interval_mid_poll(self, ask_user, monkeypatch):
        _install(monkeypatch, [
            {"request_id": "r-1", "poll_interval_seconds": 0.01},
            {"status": "input_required", "poll_interval_seconds": 0.02},
            {"status": "completed", "answer": "x"},
        ])
        assert await ask_user("q", "tok") == "x"

    @pytest.mark.asyncio
    async def test_a_blocking_backend_is_still_honoured(self, ask_user, monkeypatch):
        """Rollout safety: an old backend answers in one call, with no request_id."""
        client = _install(monkeypatch, [{"answer": "immediate"}])
        assert await ask_user("q", "tok") == "immediate"
        assert len(client.calls) == 1


class TestAskUserFailures:
    @pytest.mark.asyncio
    async def test_http_error_is_reported_with_its_status(self, ask_user, monkeypatch):
        import httpx

        err = httpx.HTTPStatusError(
            "boom", request=None, response=_Response({}, status_code=403)
        )
        _install(monkeypatch, [err])
        assert "HTTP 403" in await ask_user("q", "tok")

    @pytest.mark.asyncio
    async def test_a_request_timeout_is_a_backend_problem_now(self, ask_user, monkeypatch):
        import httpx

        _install(monkeypatch, [httpx.TimeoutException("slow")])
        # Distinct from the human-did-not-answer message: with the wait moved
        # out of the request, a timeout means the backend is unhealthy.
        assert await ask_user("q", "tok") == (
            "Unable to reach the user. Proceed with sensible defaults."
        )

    @pytest.mark.asyncio
    async def test_unexpected_errors_do_not_escape_into_the_agent(self, ask_user, monkeypatch):
        _install(monkeypatch, [RuntimeError("kaboom")])
        assert "Unable to reach the user" in await ask_user("q", "tok")

    @pytest.mark.asyncio
    async def test_a_failure_mid_poll_is_caught_too(self, ask_user, monkeypatch):
        _install(monkeypatch, [
            {"request_id": "r-1"},
            RuntimeError("dropped"),
        ])
        assert "Unable to reach the user" in await ask_user("q", "tok")


class TestServiceShape:
    def test_tool_count(self):
        assert AskUserService().tool_count == 1

    def test_asyncio_is_imported_for_the_poll_loop(self):
        assert aus.asyncio is asyncio
