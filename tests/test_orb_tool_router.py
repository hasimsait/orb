import asyncio
import json
import os
import tempfile
import unittest.mock as mock
import pytest

from orb_tool_router import OrbToolRouter, HAS_MCP_SDK


def test_config_source_list():
    config = [{"id": "test", "type": "sse", "url": "http://localhost:8000"}]
    router = OrbToolRouter(config)
    assert router.config == config


def test_config_source_json_string():
    config_json = '[{"id": "test", "type": "sse", "url": "http://localhost:8000"}]'
    router = OrbToolRouter(config_json)
    assert len(router.config) == 1
    assert router.config[0]["id"] == "test"


def test_config_source_file_path(tmp_path):
    config_data = [
        {"id": "file_test", "type": "streamable-http", "url": "http://localhost:9000"}]
    config_file = tmp_path / "mcp_config.json"
    config_file.write_text(json.dumps(config_data), encoding="utf-8")

    router = OrbToolRouter(str(config_file))
    assert len(router.config) == 1
    assert router.config[0]["id"] == "file_test"


def test_config_source_invalid_type():
    with pytest.raises(TypeError):
        OrbToolRouter(12345)


def test_initialize_without_sdk():
    async def _test():
        with mock.patch("orb_tool_router.HAS_MCP_SDK", False):
            router = OrbToolRouter([])
            with pytest.raises(RuntimeError) as exc_info:
                await router.initialize()
            assert "official 'mcp' SDK is not installed" in str(exc_info.value)

    asyncio.run(_test())


def test_initialize_unsupported_transport():
    async def _test():
        config = [{"id": "bad_server", "type": "websocket",
                   "url": "ws://localhost:8000"}]
        router = OrbToolRouter(config)

        with mock.patch("orb_tool_router.HAS_MCP_SDK", True), \
                mock.patch("orb_tool_router.AsyncExitStack") as mock_stack_cls:

            mock_stack = mock.AsyncMock()
            mock_stack_cls.return_value = mock_stack

            tools = await router.initialize()
            assert tools == []

    asyncio.run(_test())


def test_initialize_and_schema_conversion():
    async def _test():
        config = [
            {"id": "s1", "type": "streamable-http",
                "url": "http://localhost:8080"},
            {"id": "s2", "type": "sse", "url": "http://localhost:8081"},
        ]
        router = OrbToolRouter(config)

        # Mock tool response
        mock_tool = mock.MagicMock()
        mock_tool.name = "calculate"
        mock_tool.description = "Perform standard math calculations"
        mock_tool.inputSchema = {"type": "object",
                                 "properties": {"expr": {"type": "string"}}}

        mock_session = mock.AsyncMock()
        mock_tools_response = mock.MagicMock()
        mock_tools_response.tools = [mock_tool]
        mock_session.list_tools.return_value = mock_tools_response

        with mock.patch("orb_tool_router.HAS_MCP_SDK", True), \
                mock.patch("orb_tool_router.streamable_http_client", create=True) as mock_http, \
                mock.patch("orb_tool_router.sse_client", create=True) as mock_sse, \
                mock.patch("orb_tool_router.ClientSession", create=True) as mock_session_cls, \
                mock.patch("orb_tool_router.AsyncExitStack") as mock_stack_cls:

            mock_stack = mock.AsyncMock()
            mock_stack_cls.return_value = mock_stack
            mock_stack.enter_async_context.side_effect = [
                ("r1", "w1"), mock_session,  # s1
                ("r2", "w2"), mock_session,  # s2
            ]

            tools = await router.initialize()

            assert len(tools) == 2
            assert tools[0]["type"] == "function"
            assert tools[0]["function"]["name"] == "calculate"
            assert tools[0]["function"]["description"] == "Perform standard math calculations"
            assert tools[0]["function"]["parameters"] == {
                "type": "object", "properties": {"expr": {"type": "string"}}}
            assert "calculate" in router._routes

    asyncio.run(_test())


def test_execute_tool_success_and_unknown():
    async def _test():
        router = OrbToolRouter([])
        mock_session = mock.AsyncMock()
        mock_session.call_tool.return_value = "Tool execution result"
        router._routes["my_tool"] = mock_session

        # Successful call
        result = await router.execute_tool("my_tool", {"arg": "val"})
        mock_session.call_tool.assert_called_once_with(
            name="my_tool", arguments={"arg": "val"})
        assert result == "Tool execution result"

        # Unknown tool call
        with pytest.raises(KeyError) as exc_info:
            await router.execute_tool("nonexistent_tool")
        assert "Tool 'nonexistent_tool' not found" in str(exc_info.value)

    asyncio.run(_test())


def test_shutdown_and_context_manager():
    async def _test():
        router = OrbToolRouter([])
        mock_stack = mock.AsyncMock()
        router._exit_stack = mock_stack
        router._routes["dummy"] = "session"
        router.tools.append({"dummy": "tool"})

        await router.shutdown()

        mock_stack.aclose.assert_called_once()
        assert router._exit_stack is None
        assert len(router._routes) == 0
        assert len(router.tools) == 0

    asyncio.run(_test())
