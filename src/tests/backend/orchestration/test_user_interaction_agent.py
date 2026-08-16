"""Unit tests for backend.orchestration.user_interaction_agent.

Exercises create_user_interaction_agent by patching the (framework-provided)
Agent and MCPStreamableHTTPTool symbols plus MCPConfig.from_env, so the
factory's body runs without a live MCP server.
"""

from contextlib import AsyncExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import backend.orchestration.user_interaction_agent as uia
from backend.orchestration.user_interaction_agent import create_user_interaction_agent


@pytest.mark.asyncio
async def test_create_user_interaction_agent():
    fake_cfg = SimpleNamespace(name="mcp-user", url="https://host/user_responses/mcp")
    sentinel_agent = MagicMock(name="Agent")
    tool_instance = AsyncMock()  # supports async context manager protocol

    # resource_tokens is patched explicitly rather than left to whatever the
    # rest of the suite has done to sys.modules: minting is exercised for real
    # in test_resource_tokens.py, and what matters here is *what the agent is
    # asked to mint*.
    fake_tokens = MagicMock(
        PURPOSE_CLARIFY="clarify",
        DEFAULT_CLARIFY_TTL_SECONDS=3600,
    )
    fake_tokens.mint.return_value = "signed-token-value"

    with patch.object(uia.MCPConfig, "from_env", return_value=fake_cfg) as from_env, \
        patch.object(uia, "MCPStreamableHTTPTool", return_value=tool_instance) as tool_cls, \
        patch.object(uia, "resource_tokens", fake_tokens), \
        patch.object(uia, "Agent", return_value=sentinel_agent) as agent_cls:
        agent, stack = await create_user_interaction_agent(
            chat_client=MagicMock(), user_id="user-123"
        )

    from_env.assert_called_once_with(domain="user_responses")
    tool_cls.assert_called_once_with(name="mcp-user", url="https://host/user_responses/mcp")
    assert agent is sentinel_agent
    assert isinstance(stack, AsyncExitStack)

    # The agent carries a signed clarify token, not the raw user id. The id
    # must NOT appear in the prompt: a model that could read it could also
    # emit a different one, which is the defect the token replaces.
    _, kwargs = agent_cls.call_args
    assert kwargs["name"] == "UserInteractionAgent"
    assert "user-123" not in kwargs["instructions"]
    assert "SESSION_CLARIFY_TOKEN:" in kwargs["instructions"]
    assert kwargs["tools"] == [tool_instance]

    # The token in the prompt is the one minted for this user, for the
    # clarify purpose — that binding is what stops a model naming someone else.
    assert "signed-token-value" in kwargs["instructions"]
    fake_tokens.mint.assert_called_once_with(
        "clarify", subject="", user_id="user-123", ttl_seconds=3600
    )

    await stack.aclose()
