import io
import json
import os
import subprocess
import unittest.mock as mock
import pytest

from client import ChatSession, ToolManager


# ==============================================================================
# 1. INTEGRATION TEST: FULL CHAT TURN STREAMING
# ==============================================================================

def test_chat_session_stream_turn():
    session = ChatSession()
    session.messages.append({"role": "user", "content": "What is the speed of light?"})

    # Simulated SSE lines from local LLM backend (localhost:8080)
    sse_response_body = (
        'data: {"choices": [{"delta": {"reasoning_content": "Thinking about light speed..."}}]}\n\n'
        'data: {"choices": [{"delta": {"content": "The speed of light is 299,792,458 m/s."}}]}\n\n'
        'data: [DONE]\n\n'
    ).encode("utf-8")

    mock_resp = mock.MagicMock()
    mock_resp.__iter__.return_value = sse_response_body.splitlines(keepends=True)

    with mock.patch("urllib.request.urlopen") as mock_urlopen:
        mock_ctx = mock.MagicMock()
        mock_ctx.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_ctx

        session.stream_turn()

    # Verify message history state
    assert len(session.messages) == 3  # System, User, Assistant
    assert session.messages[-1]["role"] == "assistant"
    assert session.messages[-1]["content"] == "The speed of light is 299,792,458 m/s."
    assert session.pending_tool_response is False


# ==============================================================================
# 2. INTEGRATION TEST: TOOL CALL LOOP IN CHAT SESSION
# ==============================================================================

def test_chat_session_tool_call_flow():
    session = ChatSession()
    session.messages.append({"role": "user", "content": "Search for Python news"})

    sse_tool_call_body = (
        'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_abc123", "function": {"name": "web_search_exa", "arguments": "{\\"query\\": \\"Python news\\"}"}}]}}]}\n\n'
        'data: [DONE]\n\n'
    ).encode("utf-8")

    mock_resp = mock.MagicMock()
    mock_resp.__iter__.return_value = sse_tool_call_body.splitlines(keepends=True)

    with mock.patch("urllib.request.urlopen") as mock_urlopen, \
         mock.patch.object(ToolManager, "execute") as mock_tool_execute:

        mock_ctx = mock.MagicMock()
        mock_ctx.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_ctx

        mock_tool_execute.return_value = "Title: Python 3.14 Released\nURL: https://python.org\nSummary: New features included."

        session.stream_turn()

    # Verify tool execution was triggered with correct arguments
    mock_tool_execute.assert_called_once_with("web_search_exa", '{"query": "Python news"}')

    # Verify message history contains assistant tool call & tool result
    assert session.messages[-2]["role"] == "assistant"
    assert session.messages[-2]["tool_calls"][0]["id"] == "call_abc123"
    assert session.messages[-2]["tool_calls"][0]["function"]["name"] == "web_search_exa"

    assert session.messages[-1]["role"] == "tool"
    assert session.messages[-1]["tool_call_id"] == "call_abc123"
    assert "Python 3.14 Released" in session.messages[-1]["content"]

    assert session.pending_tool_response is True


# ==============================================================================
# 3. INTEGRATION TEST: EXA MCP HANDSHAKE
# ==============================================================================

def test_exa_mcp_handshake_flow():
    responses = [
        # Response 1: initialize response with session ID header
        (b'{"jsonrpc":"2.0","id":1,"result":{"capabilities":{}}}', {"mcp-session-id": "session_xyz_789"}),
        # Response 2: notification response
        (b'{"jsonrpc":"2.0"}', {}),
        # Response 3: tool call response (SSE format)
        (b'data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"Title: Test Exa Result"}]}}\n\n', {}),
    ]

    request_headers_recorded = []

    def mock_urlopen_side_effect(req, timeout=None):
        headers = dict(req.headers)
        request_headers_recorded.append(headers)

        body_bytes, resp_headers = responses.pop(0)
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = body_bytes
        mock_resp.headers = resp_headers

        mock_ctx = mock.MagicMock()
        mock_ctx.__enter__.return_value = mock_resp
        return mock_ctx

    with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen_side_effect):
        result = ToolManager._call_exa_mcp("web_search_exa", {"query": "test query"})

        assert result is not None
        assert "Title: Test Exa Result" in result

        # Verify session ID was captured in step 1 and passed in steps 2 and 3
        assert "mcp-session-id" in request_headers_recorded[1] or "Mcp-session-id" in request_headers_recorded[1]
        assert "mcp-session-id" in request_headers_recorded[2] or "Mcp-session-id" in request_headers_recorded[2]


# ==============================================================================
# 4. INTEGRATION TEST: SHELL SCRIPT SYNTAX VALIDATION
# ==============================================================================

def test_shell_scripts_syntax():
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    scripts = ["orb_action.sh", "orb_hover.sh", "orb_leave.sh", "start_orb.sh"]

    for script_name in scripts:
        script_path = os.path.join(script_dir, script_name)
        assert os.path.exists(script_path), f"Script {script_name} does not exist"

        # Check syntax using bash -n
        res = subprocess.run(["bash", "-n", script_path], capture_output=True, text=True)
        assert res.returncode == 0, f"Syntax error in {script_name}:\n{res.stderr}"
