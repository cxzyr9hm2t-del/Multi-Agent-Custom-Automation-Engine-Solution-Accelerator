# Copyright (c) Microsoft. All rights reserved.
"""Tests for services/plan_service.py."""

import json
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add src/backend to sys.path so flat imports inside plan_service resolve
_backend_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..', 'backend')
)
if _backend_path not in sys.path:
    sys.path.insert(0, _backend_path)

# Mock Azure modules
sys.modules.setdefault('azure', MagicMock())
sys.modules.setdefault('azure.ai', MagicMock())
sys.modules.setdefault('azure.ai.projects', MagicMock())
sys.modules.setdefault('azure.ai.projects.aio', MagicMock())

# The modules replaced below are real ones, and the replacements are installed
# at import time because plan_service binds them when *it* is imported. What
# was missing is putting them back: a MagicMock left in sys.modules is inherited
# by every test that imports the same name afterwards, and answers any attribute
# truthily rather than failing. That cost real time — a backfill script's
# extraction helper was silently mocked out, so its tests passed alone and
# failed in the full suite, reporting a clean run over an untouched database.
#
# Captured here, before the first replacement, and restored in teardown_module
# at the bottom of this file. Note the limit: teardown runs after this module's
# tests, so it repairs imports made during later *runs*, not during collection.
# A module that imports a real common.* at collection time still needs to guard
# itself — see src/tests/backend/test_backfill_image_ownership.py.
#
# The four azure.* names above are deliberately not in this list. They go in
# via setdefault, so they never displace a real module, and `azure` is a shared
# third-party namespace the rest of the suite relies on being present for the
# whole session — restoring it here would break the files collected after this
# one. Only first-party names, which this file does clobber, are restored.
_STUBBED_MODULES = (
    'common.config.app_config',
    'common.database.database_factory',
    'common.utils.event_utils',
    'common.utils.image_assets',
    'common.models.messages',
    'models',
    'models.messages',
    'orchestration',
    'orchestration.connection_config',
)
_ORIGINAL_MODULES = {name: sys.modules.get(name) for name in _STUBBED_MODULES}

# Mock common modules
mock_config_module = MagicMock()
mock_config = MagicMock()
mock_config.DATABASE_TYPE = 'memory'
mock_config_module.config = mock_config
sys.modules['common.config.app_config'] = mock_config_module

mock_database_factory = MagicMock()
sys.modules['common.database.database_factory'] = mock_database_factory

mock_event_utils = MagicMock()
sys.modules['common.utils.event_utils'] = mock_event_utils

# Ownership recording is fire-and-forget from this service's point of view.
mock_image_assets = MagicMock()
mock_image_assets.record_image_ownership = AsyncMock()
sys.modules['common.utils.image_assets'] = mock_image_assets

# Create mock common.models.messages with enums
class MockAgentType:
    HUMAN = MagicMock()
    HUMAN.value = "Human_Agent"

class MockAgentMessageType:
    HUMAN_AGENT = "Human_Agent"
    AI_AGENT = "AI_Agent"

class MockPlanStatus:
    approved = "approved"
    completed = "completed"
    rejected = "rejected"

class MockAgentMessageData:
    def __init__(self, plan_id, user_id, m_plan_id, agent, agent_type, content, raw_data, steps, next_steps):
        self.plan_id = plan_id
        self.user_id = user_id
        self.m_plan_id = m_plan_id
        self.agent = agent
        self.agent_type = agent_type
        self.content = content
        self.raw_data = raw_data
        self.steps = steps
        self.next_steps = next_steps

mock_messages_common = MagicMock()
mock_messages_common.AgentType = MockAgentType
mock_messages_common.AgentMessageType = MockAgentMessageType
mock_messages_common.PlanStatus = MockPlanStatus
mock_messages_common.AgentMessageData = MockAgentMessageData
sys.modules['common.models.messages'] = mock_messages_common

# Mock models.messages (flat import used by plan_service after migration)
mock_v_messages = MagicMock()
sys.modules['models'] = MagicMock()
sys.modules['models.messages'] = mock_v_messages

# Mock orchestration.connection_config
mock_orchestration_config = MagicMock()
mock_orchestration_config.plans = {}
mock_orchestration_module = MagicMock()
mock_orchestration_module.orchestration_config = mock_orchestration_config
sys.modules['orchestration'] = MagicMock()
sys.modules['orchestration.connection_config'] = mock_orchestration_module

import backend.services.plan_service as plan_service_module
from backend.services.plan_service import (
    PlanService, build_agent_message_from_agent_message_response,
    build_agent_message_from_user_clarification)

# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------

@dataclass
class MockUserClarificationResponse:
    plan_id: str = ""
    m_plan_id: str = ""
    answer: str = ""


@dataclass
class MockAgentMessageResponse:
    plan_id: str = ""
    user_id: str = ""
    m_plan_id: str = ""
    agent: str = ""
    agent_name: str = ""
    source: str = ""
    agent_type: Any = None
    content: str = ""
    text: str = ""
    raw_data: Any = None
    steps: List = None
    next_steps: List = None
    is_final: bool = False
    streaming_message: str = ""


@dataclass
class MockPlanApprovalResponse:
    plan_id: str = ""
    m_plan_id: str = ""
    approved: bool = True
    feedback: str = ""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestUtilityFunctions:
    def test_build_agent_message_from_user_clarification_basic(self):
        feedback = MockUserClarificationResponse(
            plan_id="test-plan-123",
            m_plan_id="test-m-plan-456",
            answer="This is my clarification"
        )
        result = build_agent_message_from_user_clarification(feedback, "test-user-789")
        assert result.plan_id == "test-plan-123"
        assert result.user_id == "test-user-789"
        assert result.m_plan_id == "test-m-plan-456"
        assert result.agent == "Human_Agent"
        assert result.content == "This is my clarification"
        assert result.steps == []
        assert result.next_steps == []

    def test_build_agent_message_from_user_clarification_empty_fields(self):
        feedback = MockUserClarificationResponse(plan_id=None, m_plan_id=None, answer=None)
        result = build_agent_message_from_user_clarification(feedback, "test-user")
        assert result.plan_id == ""
        assert result.user_id == "test-user"
        assert result.m_plan_id is None
        assert result.content == ""

    def test_build_agent_message_from_user_clarification_raw_data_serialization(self):
        feedback = MockUserClarificationResponse(plan_id="test-plan", answer="test answer")
        result = build_agent_message_from_user_clarification(feedback, "test-user")
        raw_data = json.loads(result.raw_data)
        assert raw_data["plan_id"] == "test-plan"
        assert raw_data["answer"] == "test answer"

    def test_build_agent_message_from_agent_message_response_basic(self):
        response = MockAgentMessageResponse(
            plan_id="test-plan-123",
            user_id="response-user",
            agent="TestAgent",
            content="Agent response content",
            steps=["step1", "step2"],
            next_steps=["next1"]
        )
        result = build_agent_message_from_agent_message_response(response, "fallback-user")
        assert result.plan_id == "test-plan-123"
        assert result.user_id == "response-user"
        assert result.agent == "TestAgent"
        assert result.content == "Agent response content"
        assert result.steps == ["step1", "step2"]
        assert result.next_steps == ["next1"]

    def test_build_agent_message_from_agent_message_response_fallbacks(self):
        response = MockAgentMessageResponse(
            plan_id="",
            user_id="",
            agent="",
            agent_name="NamedAgent",
            text="Text content",
            steps=None,
            next_steps=None
        )
        result = build_agent_message_from_agent_message_response(response, "fallback-user")
        assert result.user_id == "fallback-user"
        assert result.agent == "NamedAgent"
        assert result.content == "Text content"
        assert result.steps == []
        assert result.next_steps == []

    def test_build_agent_message_from_agent_message_response_agent_type_inference(self):
        response_human = MockAgentMessageResponse(agent_type="human_agent")
        result = build_agent_message_from_agent_message_response(response_human, "user")
        assert result.agent_type == MockAgentMessageType.HUMAN_AGENT

        response_ai = MockAgentMessageResponse(agent_type="unknown")
        result = build_agent_message_from_agent_message_response(response_ai, "user")
        assert result.agent_type == MockAgentMessageType.AI_AGENT

    def test_build_agent_message_from_agent_message_response_raw_data_dict(self):
        response = MockAgentMessageResponse(raw_data={"test": "data"})
        result = build_agent_message_from_agent_message_response(response, "user")
        assert '"test": "data"' in result.raw_data

    def test_build_agent_message_from_agent_message_response_raw_data_none(self):
        response = MockAgentMessageResponse(raw_data=None, content="test")
        result = build_agent_message_from_agent_message_response(response, "user")
        assert isinstance(result.raw_data, str)

    def test_build_agent_message_from_agent_message_response_source_fallback(self):
        response = MockAgentMessageResponse(agent="", agent_name="", source="SourceAgent")
        result = build_agent_message_from_agent_message_response(response, "user")
        assert result.agent == "SourceAgent"


class TestPlanService:
    @pytest.mark.asyncio
    async def test_handle_plan_approval_success(self):
        mock_approval = MockPlanApprovalResponse(
            plan_id="test-plan-123",
            m_plan_id="test-m-plan-456",
            approved=True,
            feedback="Looks good!"
        )
        mock_mplan = MagicMock()
        mock_mplan.plan_id = None
        mock_mplan.team_id = None
        mock_mplan.model_dump.return_value = {"test": "data"}
        mock_orchestration_config.plans = {"test-m-plan-456": mock_mplan}

        mock_db = MagicMock()
        mock_plan = MagicMock()
        mock_plan.team_id = "test-team"
        mock_db.get_plan = AsyncMock(return_value=mock_plan)
        mock_db.update_plan = AsyncMock()
        mock_database_factory.DatabaseFactory.get_database = AsyncMock(return_value=mock_db)

        with patch.object(plan_service_module, 'orchestration_config', mock_orchestration_config):
            result = await PlanService.handle_plan_approval(mock_approval, "test-user")

        assert result is True
        assert mock_mplan.plan_id == "test-plan-123"
        assert mock_plan.overall_status == MockPlanStatus.approved
        mock_db.update_plan.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_plan_approval_rejection(self):
        mock_approval = MockPlanApprovalResponse(
            plan_id="test-plan-123",
            m_plan_id="test-m-plan-456",
            approved=False
        )
        mock_mplan = MagicMock()
        mock_mplan.plan_id = "existing-plan-id"
        mock_orchestration_config.plans = {"test-m-plan-456": mock_mplan}

        mock_db = MagicMock()
        mock_db.delete_plan_by_plan_id = AsyncMock()
        mock_database_factory.DatabaseFactory.get_database = AsyncMock(return_value=mock_db)

        with patch.object(plan_service_module, 'orchestration_config', mock_orchestration_config):
            result = await PlanService.handle_plan_approval(mock_approval, "test-user")

        assert result is True
        mock_db.delete_plan_by_plan_id.assert_called_once_with("test-plan-123")

    @pytest.mark.asyncio
    async def test_handle_plan_approval_no_orchestration_config(self):
        mock_approval = MockPlanApprovalResponse()
        with patch.object(plan_service_module, 'orchestration_config', None):
            result = await PlanService.handle_plan_approval(mock_approval, "user")
        assert result is False

    @pytest.mark.asyncio
    async def test_handle_plan_approval_plan_not_found(self):
        mock_approval = MockPlanApprovalResponse(
            plan_id="missing-plan",
            m_plan_id="test-m-plan",
            approved=True
        )
        mock_mplan = MagicMock()
        mock_mplan.plan_id = None
        mock_orchestration_config.plans = {"test-m-plan": mock_mplan}

        mock_db = MagicMock()
        mock_db.get_plan = AsyncMock(return_value=None)
        mock_database_factory.DatabaseFactory.get_database = AsyncMock(return_value=mock_db)

        with patch.object(plan_service_module, 'orchestration_config', mock_orchestration_config):
            result = await PlanService.handle_plan_approval(mock_approval, "user")

        assert result is False

    @pytest.mark.asyncio
    async def test_handle_plan_approval_exception(self):
        mock_approval = MockPlanApprovalResponse(m_plan_id="nonexistent")
        mock_orchestration_config.plans = {}
        with patch.object(plan_service_module, 'orchestration_config', mock_orchestration_config):
            result = await PlanService.handle_plan_approval(mock_approval, "user")
        assert result is False

    @pytest.mark.asyncio
    async def test_handle_agent_messages_success(self):
        mock_message = MockAgentMessageResponse(
            plan_id="test-plan",
            agent="TestAgent",
            content="Agent message content",
            is_final=False
        )
        mock_db = MagicMock()
        mock_db.add_agent_message = AsyncMock()
        mock_database_factory.DatabaseFactory.get_database = AsyncMock(return_value=mock_db)

        result = await PlanService.handle_agent_messages(mock_message, "test-user")

        assert result is True
        mock_db.add_agent_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_agent_messages_final_message(self):
        mock_message = MockAgentMessageResponse(
            plan_id="test-plan",
            agent="TestAgent",
            content="Final message",
            is_final=True,
            streaming_message="Stream completed"
        )
        mock_db = MagicMock()
        mock_plan = MagicMock()
        mock_db.add_agent_message = AsyncMock()
        mock_db.get_plan = AsyncMock(return_value=mock_plan)
        mock_db.update_plan = AsyncMock()
        mock_database_factory.DatabaseFactory.get_database = AsyncMock(return_value=mock_db)

        result = await PlanService.handle_agent_messages(mock_message, "test-user")

        assert result is True
        assert mock_plan.streaming_message == "Stream completed"
        assert mock_plan.overall_status == MockPlanStatus.completed
        mock_db.update_plan.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_agent_messages_final_message_plan_not_found(self):
        """A final message for a plan the caller does not own must not succeed.

        get_plan is scoped to the caller, so a foreign or missing plan_id yields
        None. This previously dereferenced None and reported the resulting
        AttributeError as success, leaving the caller believing the plan was
        marked completed.
        """
        mock_message = MockAgentMessageResponse(
            plan_id="someone-elses-plan",
            agent="TestAgent",
            content="Final message",
            is_final=True,
            streaming_message="Stream completed"
        )
        mock_db = MagicMock()
        mock_db.add_agent_message = AsyncMock()
        mock_db.get_plan = AsyncMock(return_value=None)
        mock_db.update_plan = AsyncMock()
        mock_database_factory.DatabaseFactory.get_database = AsyncMock(return_value=mock_db)

        result = await PlanService.handle_agent_messages(mock_message, "test-user")

        assert result is False
        mock_db.update_plan.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_agent_messages_exception(self):
        mock_message = MockAgentMessageResponse()
        mock_database_factory.DatabaseFactory.get_database = AsyncMock(
            side_effect=Exception("Database error")
        )
        result = await PlanService.handle_agent_messages(mock_message, "user")
        assert result is False

    @pytest.mark.asyncio
    async def test_handle_human_clarification_success(self):
        mock_clarification = MockUserClarificationResponse(
            plan_id="test-plan",
            answer="This is my clarification"
        )
        mock_db = MagicMock()
        mock_db.add_agent_message = AsyncMock()
        mock_database_factory.DatabaseFactory.get_database = AsyncMock(return_value=mock_db)

        result = await PlanService.handle_human_clarification(mock_clarification, "test-user")

        assert result is True
        mock_db.add_agent_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_human_clarification_exception(self):
        mock_clarification = MockUserClarificationResponse()
        mock_database_factory.DatabaseFactory.get_database = AsyncMock(
            side_effect=Exception("Database error")
        )
        result = await PlanService.handle_human_clarification(mock_clarification, "user")
        assert result is False

    @pytest.mark.asyncio
    async def test_static_method_properties(self):
        mock_approval = MockPlanApprovalResponse(approved=False)
        with patch.object(plan_service_module, 'orchestration_config', None):
            result = await PlanService.handle_plan_approval(mock_approval, "user")
        assert result is False

    def test_logging_integration(self):
        logger = logging.getLogger('backend.services.plan_service')
        assert logger is not None


def teardown_module(module):
    """Put back the modules this file replaced in sys.modules at import time.

    Without this the MagicMocks outlive the file that installed them and are
    inherited by anything importing the same names later. See the note beside
    _STUBBED_MODULES at the top for what that cost.
    """
    for name, original in _ORIGINAL_MODULES.items():
        if original is None:
            # Nothing was there before, so drop the stub entirely and let the
            # next import load the real module.
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original
