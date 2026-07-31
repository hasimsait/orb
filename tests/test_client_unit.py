import io
import json
import os
import sys
import unittest.mock as mock
import pytest

from client import (
    expand_file_references,
    StreamState,
    StreamUI,
    ChatSession,
    ToolManager,
    COLOR_DIM,
    COLOR_RESET,
    SEPARATOR,
)


# ==============================================================================
# 1. TEST EXPAND_FILE_REFERENCES
# ==============================================================================

def test_expand_file_references_empty():
    assert expand_file_references("") == ""
    assert expand_file_references(None) is None


def test_expand_file_references_no_at_token():
    prompt = "Hello world, how are you?"
    assert expand_file_references(prompt) == prompt


def test_expand_file_references_nonexistent_file(tmp_path):
    missing_path = tmp_path / "does_not_exist.txt"
    prompt = f"Check @{missing_path} for details"
    result = expand_file_references(prompt)
    assert result == prompt


def test_expand_file_references_valid_small_file(sample_files):
    small_path = sample_files["small"]
    prompt = f"Please analyze @{small_path} and advise."
    result = expand_file_references(prompt)

    assert f"--- File: {small_path} ---" in result
    assert "Hello from small file!" in result
    assert "--- End File ---" in result


def test_expand_file_references_large_file_truncation(sample_files):
    large_path = sample_files["large"]
    prompt = f"Analyze @{large_path}"
    result = expand_file_references(prompt)

    assert f"--- File: {large_path} ---" in result
    assert "...[Truncated]..." in result


def test_expand_file_references_trailing_punctuation(sample_files):
    small_path = sample_files["small"]
    prompt = f"Look at @{small_path}, please!"
    result = expand_file_references(prompt)

    assert f"--- File: {small_path} ---" in result
    assert "Hello from small file!" in result
    # Trailing punctuation is stripped from the clean_path, so the token match replaces "@path,"
    assert " please!" in result


def test_expand_file_references_multiple_files(sample_files):
    file1 = sample_files["small"]
    file2 = sample_files["nested"]
    prompt = f"Diff @{file1} with @{file2}"
    result = expand_file_references(prompt)

    assert f"--- File: {file1} ---" in result
    assert "Hello from small file!" in result
    assert f"--- File: {file2} ---" in result
    assert "print('nested')" in result


# ==============================================================================
# 2. TEST STREAM_UI AND STREAM_STATE
# ==============================================================================

def test_stream_ui_initial_state():
    ui = StreamUI()
    assert ui.state == StreamState.START
    assert ui.reply == ""


def test_stream_ui_transition(capsys):
    ui = StreamUI()

    ui.transition(StreamState.THOUGHT)
    assert ui.state == StreamState.THOUGHT
    captured = capsys.readouterr()
    assert captured.out == COLOR_DIM

    ui.transition(StreamState.RESPONSE)
    assert ui.state == StreamState.RESPONSE
    captured = capsys.readouterr()
    assert captured.out == SEPARATOR + COLOR_RESET


def test_stream_ui_process_chunk_thought_tags(capsys):
    ui = StreamUI()
    # Step 1: send thought tags
    ui.process_chunk("<think>Calculating logic</think>")
    assert ui.state == StreamState.RESPONSE

    # Step 2: send response text chunk while in RESPONSE state
    ui.process_chunk("Here is the answer")

    assert ui.reply == "Here is the answer"
    captured = capsys.readouterr()
    assert "Calculating logic" in captured.out
    assert "Here is the answer" in captured.out


def test_stream_ui_process_chunk_response_direct(capsys):
    ui = StreamUI()
    ui.process_chunk("Direct response text")

    assert ui.state == StreamState.RESPONSE
    assert ui.reply == "Direct response text"
    captured = capsys.readouterr()
    assert "Direct response text" in captured.out


# ==============================================================================
# 3. TEST CHATSESSION._SAFE_SPLIT_TAGS
# ==============================================================================

def test_safe_split_tags_complete():
    session = ChatSession.__new__(ChatSession)
    safe, tail = session._safe_split_tags("Hello <think>content</think> world")
    assert safe == "Hello <think>content</think> world"
    assert tail == ""


def test_safe_split_tags_incomplete_open_tag():
    session = ChatSession.__new__(ChatSession)
    safe, tail = session._safe_split_tags("Hello <thin")
    assert safe == "Hello "
    assert tail == "<thin"


def test_safe_split_tags_incomplete_long_tail():
    session = ChatSession.__new__(ChatSession)
    long_tag = "<" + "a" * 40
    safe, tail = session._safe_split_tags(f"Hello {long_tag}")
    assert safe == f"Hello {long_tag}"
    assert tail == ""


# ==============================================================================
# 4. TEST TOOLMANAGER EXA SEARCH OUTPUT PROCESSING
# ==============================================================================

def test_process_exa_search_output_empty():
    assert ToolManager._process_exa_search_output("") == ""
    assert ToolManager._process_exa_search_output(None) is None


def test_process_exa_search_output_json_array():
    input_json = json.dumps([
        {"title": "Result 1", "url": "https://r1.com",
            "text": "This is sample result one text."},
        {"title": "Result 2", "url": "https://r2.com",
            "snippet": "Sample result two snippet."}
    ])
    processed = ToolManager._process_exa_search_output(
        input_json, max_results=2, max_chars_per_result=100)

    assert "Title: Result 1" in processed
    assert "URL: https://r1.com" in processed
    assert "Summary: This is sample result one text." in processed
    assert "Title: Result 2" in processed


def test_process_exa_search_output_markdown():
    raw_markdown = """
Title: Page One
URL: https://page1.com
Content for page one.

---

Title: Page Two
URL: https://page2.com
Content for page two.
"""
    processed = ToolManager._process_exa_search_output(
        raw_markdown, max_results=1, max_chars_per_result=50)

    assert "Title: Page One" in processed
    assert "Page Two" not in processed


# ==============================================================================
# 5. TEST TOOLMANAGER FALLBACKS (WIKIPEDIA & HTTP FETCH)
# ==============================================================================

def test_search_wikipedia_empty():
    res = json.loads(ToolManager._search_wikipedia(""))
    assert "error" in res


def test_search_wikipedia_success():
    mock_response = io.BytesIO(json.dumps({
        "query": {
            "search": [
                {"title": "Python (programming language)",
                 "snippet": "Python is a <span>high-level</span> language."}
            ]
        }
    }).encode("utf-8"))

    with mock.patch("urllib.request.urlopen") as mock_urlopen:
        mock_ctx = mock.MagicMock()
        mock_ctx.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_ctx

        res_str = ToolManager._search_wikipedia("Python")
        res = json.loads(res_str)

        assert "results" in res
        assert len(res["results"]) == 1
        assert res["results"][0]["title"] == "Python (programming language)"
        # HTML tag <span> should be stripped
        assert res["results"][0]["snippet"] == "Python is a high-level language."


def test_fallback_fetch_empty():
    res = json.loads(ToolManager._fallback_fetch([]))
    assert "error" in res


def test_fallback_fetch_success():
    html_content = """
    <html>
        <head><style>body { color: red; }</style></head>
        <body>
            <script>console.log("hello");</script>
            <h1>Test Header</h1>
            <p>Some paragraph text.</p>
        </body>
    </html>
    """
    mock_response = io.BytesIO(html_content.encode("utf-8"))

    with mock.patch("urllib.request.urlopen") as mock_urlopen:
        mock_ctx = mock.MagicMock()
        mock_ctx.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_ctx

        res_str = ToolManager._fallback_fetch("https://example.com")
        res = json.loads(res_str)

        assert "results" in res
        assert len(res["results"]) == 1
        text = res["results"][0]["text"]
        assert "Test Header Some paragraph text." in text
        assert "color: red" not in text
        assert "console.log" not in text


# ==============================================================================
# 6. TEST STDLIB LOGGING INTEGRATION
# ==============================================================================

def test_stdlib_logging_integration(tmp_path):
    log_file = tmp_path / "stdlib_test.log"
    from client import logger as orb_logger, log, ERROR, WARN, DEBUG, TRACE, _setup_logger

    old_handlers = list(orb_logger.handlers)
    orb_logger.handlers.clear()
    try:
        with mock.patch.dict(os.environ, {"ORB_LOG": "trace", "ORB_LOG_FILE": str(log_file)}):
            _setup_logger()
            log(DEBUG, "Test stdlib logging debug")
            log(ERROR, "Test stdlib logging error")

        content = log_file.read_text(encoding="utf-8")
        assert "Test stdlib logging debug" in content
        assert "Test stdlib logging error" in content
    finally:
        for h in list(orb_logger.handlers):
            h.close()
        orb_logger.handlers = old_handlers
