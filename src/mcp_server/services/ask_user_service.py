"""
Human-in-the-loop MCP tool — ask_user.

Provides an ``ask_user`` tool that any domain agent can call to request
clarification from the human user. The tool POSTs the question to the backend,
which relays it over WebSocket to the browser, then polls for the answer.

**Why it polls.** It used to hold a single HTTP request open for the whole time
the human took to answer — up to five minutes, across the backend's public
ingress. Any idle timeout anywhere in that path (ingress, proxy, load balancer)
would drop the connection and discard a clarification the user was in the middle
of answering, and the agent would be told nobody replied. Waiting between short
requests rather than inside one long request removes that failure, and matches
the shape MCP itself moved to: task polling in the 2025-11-25 spec, and
Multi Round-Trip Requests in 2026-07-28. See
docs/reports/2026-08-16-mrtr-migration-path.md.

The answer is returned as a plain string — the agent continues with it
in context like any other tool result.
"""

import asyncio
import logging
import os
import time

import httpx
from core.factory import MCPToolBase

logger = logging.getLogger(__name__)

# The backend URL is needed so the MCP server can relay questions.
# In local dev this is typically http://localhost:8000; in Azure it is
# the App Service URL.  Falls back to localhost for convenience.
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")

# How long to keep asking before giving up on the human. This is a wall-clock
# budget spanning many short requests, not the timeout of any one of them.
ASK_USER_TIMEOUT = float(os.environ.get("ASK_USER_TIMEOUT", "300"))

# Timeout for each individual HTTP call to the backend. Short on purpose: no
# request in this flow waits for a person any more, so anything slower than this
# is the backend being unhealthy rather than a human thinking.
REQUEST_TIMEOUT = float(os.environ.get("CLARIFICATION_REQUEST_TIMEOUT", "30"))

# Used only until the backend advertises its own cadence in the response.
DEFAULT_POLL_INTERVAL = float(os.environ.get("CLARIFICATION_POLL_INTERVAL", "2"))


class AskUserService(MCPToolBase):
    """Cross-domain tool that pauses the workflow to ask the user a question."""

    def __init__(self):
        # Use a sentinel domain — this service is registered on every
        # domain server, not just one.
        from core.factory import Domain
        super().__init__(Domain.GENERAL)

    def register_tools(self, mcp) -> None:
        """Register the ask_user tool on the given FastMCP server."""

        @mcp.tool()
        async def ask_user(question: str, session_token: str) -> str:
            """Ask the human user one or more clarifying questions and return their answer.

            Call this tool when you need information that was not provided in
            the original task and cannot be discovered by any other tool.  Ask
            about ALL unknown parameters — both required and optional.

            IMPORTANT: You must call this tool AT MOST ONCE per turn.  If you
            need multiple pieces of information, combine ALL questions into the
            single ``question`` string as a numbered list.  Example:

                question: "I need a few details to proceed:\n1. Employee full name?\n2. Start date?\n3. Department?"

            Do NOT call this tool multiple times in a row.

            Args:
                question:      One or more questions formatted as a numbered
                               list. Combine all missing information into this
                               single string.
                session_token: REQUIRED — copy the EXACT value of
                               ``SESSION_CLARIFY_TOKEN`` from your system
                               instructions. It identifies the person to ask.
                               DO NOT guess or invent one, and do NOT pass a
                               user id or any other value here. If your
                               instructions do not contain
                               SESSION_CLARIFY_TOKEN, do NOT call this tool.

            Returns:
                The user's answer as a plain string.
            """
            ask_url = f"{BACKEND_URL}/api/v4/clarification/ask"
            result_url = f"{BACKEND_URL}/api/v4/clarification/result"

            # The token is a credential: log only that one was supplied.
            logger.info(
                "ask_user: relaying question to backend (token supplied: %s): %.120s",
                bool(session_token),
                question,
            )

            deadline = time.monotonic() + ASK_USER_TIMEOUT

            try:
                # Each HTTP call is short. The wait for a human happens between
                # calls, not inside one — see the module docstring.
                async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                    resp = await client.post(
                        ask_url,
                        json={"question": question, "session_token": session_token},
                    )
                    resp.raise_for_status()
                    created = resp.json()

                    request_id = created.get("request_id")
                    if not request_id:
                        # A backend still running the old blocking contract
                        # answers in one call. Honour it rather than failing.
                        answer = created.get("answer", "")
                        return answer or "The user did not provide an answer."

                    interval = float(
                        created.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL)
                    )

                    while time.monotonic() < deadline:
                        await asyncio.sleep(interval)

                        poll = await client.post(
                            result_url,
                            json={
                                "request_id": request_id,
                                "session_token": session_token,
                            },
                        )
                        poll.raise_for_status()
                        data = poll.json()
                        status = data.get("status")

                        if status == "completed":
                            answer = data.get("answer", "")
                            logger.info("ask_user: received answer: %.120s", answer)
                            return answer or "The user did not provide an answer."

                        if status in ("expired", "unknown"):
                            logger.warning(
                                "ask_user: request %s ended as '%s'.", request_id, status
                            )
                            return (
                                "The user did not respond in time. "
                                "Proceed with sensible defaults."
                            )

                        interval = float(
                            data.get("poll_interval_seconds", interval)
                        )

                    logger.warning("ask_user: timed out waiting for user response.")
                    return "The user did not respond in time. Proceed with sensible defaults."

            except httpx.TimeoutException:
                # A single request timing out is a backend problem now, not a
                # slow human — the human wait no longer happens inside a request.
                logger.error("ask_user: backend request timed out.")
                return "Unable to reach the user. Proceed with sensible defaults."
            except httpx.HTTPStatusError as exc:
                logger.error("ask_user: backend returned %s", exc.response.status_code)
                return f"Unable to reach the user (HTTP {exc.response.status_code}). Proceed with sensible defaults."
            except Exception as exc:
                logger.error("ask_user: unexpected error: %s", exc)
                return "Unable to reach the user. Proceed with sensible defaults."

    @property
    def tool_count(self) -> int:
        return 1
