#!/usr/bin/env python3
import json
import os
import re
import sys
import traceback
import urllib.parse
import urllib.request
from enum import Enum, auto
from log import log, ERROR, WARN, DEBUG, TRACE

# ==============================================================================
# CONFIGURATION & CONSTANTS
# ==============================================================================

COLOR_RESET = "\033[0m"
COLOR_DIM = "\033[90m"
COLOR_BLUE = "\033[94m"
COLOR_PURPLE = "\033[95m"
SEPARATOR = f"\n{COLOR_DIM}{'—' * 40}{COLOR_RESET}\n"


RE_OPEN_TAGS = {"<|channel>", "<channel>", "<think>", "<|thought|>"}
RE_CLOSE_TAGS = {"<channel|>", "<|channel|>", "</think>", "</thought>"}
RE_TAG = re.compile(
    r'<\|?channel\|?>|<\|?channel>|<think>|</think>|<\|thought\|>|</thought>', re.IGNORECASE)

DEFAULT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search_exa",
            "description": "Search the web for current information, news, and facts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "numResults": {"type": "number"}
                },
                "required": ["query"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch_exa",
            "description": "Read a webpage's full content as clean markdown.",
            "parameters": {
                "type": "object",
                "properties": {
                    "urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "URLs to read. Batch multiple URLs in one call."
                    },
                    "maxCharacters": {
                        "type": "number",
                        "minimum": 1,
                        "description": "Maximum characters to extract per page (default: 3000)"
                    }
                },
                "required": ["urls"],
                "additionalProperties": False
            }
        }
    }
]

# ==============================================================================
# TOOL EXECUTORS (MCP Handshake & Fallbacks)
# ==============================================================================


class ToolManager:
    @staticmethod
    def execute(name, args_str):
        """Routes tool calls to the appropriate handler."""
        try:
            args = json.loads(args_str) if args_str.strip() else {}
        except Exception:
            args = {}

        log(DEBUG, f"Tool routing: {name}  args={args_str[:120]}")

        if name in ("web_search_exa", "web_fetch_exa"):
            result = ToolManager._call_exa_mcp(name, args)
            if result:
                log(DEBUG, "Exa MCP call succeeded")
                return result

            log(DEBUG,
                f"Exa MCP returned nothing; using local fallback for {name}")
            if name == "web_search_exa":
                return ToolManager._search_wikipedia(args.get("query", ""))
            else:
                return ToolManager._fallback_fetch(args.get("urls", []), args.get("maxCharacters", 3000))

        log(ERROR, f"Unknown tool requested: {name!r}")
        return json.dumps({"status": "error", "message": f"Unknown tool '{name}'"})

    @staticmethod
    def _call_exa_mcp(name, args):
        """Executes the formal MCP HTTP Handshake to acquire a session ID before calling the tool."""
        url = "https://mcp.exa.ai/mcp"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:153.0) Gecko/20100101 Firefox/153.0",
            "mcp-protocol-version": "2025-11-25",
            "Origin": "http://127.0.0.1:8080",
            "Connection": "keep-alive"
        }

        def _post(payload):
            body = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                # Capture dynamic MCP session ID to authenticate subsequent requests
                sess_id = (resp.headers.get('mcp-session-id')
                           or resp.headers.get('Mcp-Session-Id'))
                if sess_id:
                    headers['mcp-session-id'] = sess_id
                    log(DEBUG, f"MCP session ID acquired: {sess_id[:16]}…")
                return resp.read().decode('utf-8', errors='replace')

        try:
            # 1. MCP Handshake: Initialize
            log(DEBUG, "MCP step 1/3: initialize")
            _post({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "chaos_orb", "version": "1.0.0"}
                }
            })

            # 2. MCP Handshake: Acknowledge initialization
            log(DEBUG, "MCP step 2/3: notifications/initialized")
            _post({"jsonrpc": "2.0", "method": "notifications/initialized"})

            # If web_search_exa, cap numResults in args sent to Exa MCP to avoid fetching too many results
            if name == "web_search_exa":
                args = dict(args)
                try:
                    args["numResults"] = max(1, min(int(args.get("numResults", 3)), 3))
                except (ValueError, TypeError):
                    args["numResults"] = 3

            # 3. Execution: Call the actual tool securely
            log(DEBUG, f"MCP step 3/3: tools/call → {name}")
            raw_data = _post({
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": name, "arguments": args}
            })

            # 4. Parse Server-Sent Events (SSE) or fall back to raw JSON
            texts = []
            for line in raw_data.splitlines():
                if not line.startswith("data: "):
                    continue
                try:
                    item = json.loads(line[6:])
                    content_blocks = item.get("result", {}).get("content", [])
                    texts.extend(
                        c.get("text", "")
                        for c in content_blocks
                        if c.get("type") == "text"
                    )
                except Exception:
                    pass

            log(DEBUG, f"MCP SSE parsed: {len(texts)} text block(s)")
            if texts:
                res_text = "\n".join(texts)
                if name == "web_search_exa":
                    res_text = ToolManager._process_exa_search_output(res_text)
                return res_text

            # SSE produced nothing — try treating the response as plain JSON
            log(DEBUG, "MCP SSE empty; falling back to raw JSON parse")
            res_data = json.loads(raw_data)
            content_blocks = res_data.get("result", {}).get("content", [])
            texts = [c.get("text", "")
                     for c in content_blocks if c.get("type") == "text"]
            log(DEBUG, f"MCP raw JSON parsed: {len(texts)} text block(s)")
            if not texts:
                return None
            res_text = "\n".join(texts)
            if name == "web_search_exa":
                res_text = ToolManager._process_exa_search_output(res_text)
            return res_text

        except Exception as e:
            log(WARN, f"Exa MCP failed: {e} — falling back to local handlers")
            return None

    @staticmethod
    def _process_exa_search_output(text, max_results=3, max_chars_per_result=600):
        """Reduces Exa search output size so local LLMs can process it quickly without timing out."""
        if not text:
            return text

        orig_len = len(text)
        try:
            # Try parsing as JSON first in case Exa returned serialized JSON array
            try:
                data = json.loads(text)
                if isinstance(data, list):
                    processed = []
                    for item in data[:max_results]:
                        if isinstance(item, dict):
                            title = item.get("title", "")
                            url = item.get("url", "")
                            snip = str(item.get("text") or item.get("highlights") or item.get("snippet") or "")
                            if len(snip) > max_chars_per_result:
                                snip = snip[:max_chars_per_result].rsplit(' ', 1)[0] + " … [truncated]"
                            processed.append(f"Title: {title}\nURL: {url}\nSummary: {snip}")
                        else:
                            s = str(item)
                            if len(s) > max_chars_per_result:
                                s = s[:max_chars_per_result].rsplit(' ', 1)[0] + " … [truncated]"
                            processed.append(s)
                    res = "\n\n---\n\n".join(processed)
                    log(DEBUG, f"Processed Exa JSON search output: {orig_len} → {len(res)} chars ({len(processed)} results)")
                    return res
            except Exception:
                pass

            # Otherwise treat as markdown / plain text and split into results
            # Check standard delimiters in priority order
            items = None
            for pattern in [
                r'\n+\s*(?:---|(?:\*\*\*))\s*\n+',
                r'\n+(?=(?:Title:|title:)\s)',
                r'\n+(?=#+\s)',
                r'\n+(?=\d+\.\s)'
            ]:
                parts = [p.strip() for p in re.split(pattern, text) if p.strip()]
                if len(parts) > 1:
                    items = parts
                    break

            if not items:
                # No delimiters found; split by paragraphs
                items = [p.strip() for p in re.split(r'\n\n+', text) if p.strip()]

            processed = []
            for item in items[:max_results]:
                if len(item) > max_chars_per_result:
                    item = item[:max_chars_per_result].rsplit(' ', 1)[0] + " … [truncated]"
                processed.append(item)

            res = "\n\n---\n\n".join(processed)
            max_total = max_results * max_chars_per_result
            if len(res) > max_total:
                res = res[:max_total].rsplit(' ', 1)[0] + " … [truncated]"

            log(DEBUG, f"Processed Exa text search output: {orig_len} → {len(res)} chars ({len(processed)} results)")
            return res
        except Exception as e:
            log(WARN, f"Failed to process Exa search output ({e}), truncating raw string")
            max_total = max_results * max_chars_per_result
            return text[:max_total] + " … [truncated]" if len(text) > max_total else text

    @staticmethod
    def _search_wikipedia(query):
        """Bulletproof zero-auth fallback if MCP gateway rejects the connection."""
        if not query:
            return json.dumps({"error": "No query provided."})

        log(DEBUG, f"Wikipedia fallback: query={query!r}")
        encoded_query = urllib.parse.quote(query)
        wiki_url = (
            f"https://en.wikipedia.org/w/api.php"
            f"?action=query&list=search&srsearch={encoded_query}&utf8=&format=json"
        )
        req = urllib.request.Request(
            wiki_url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                raw_items = data.get("query", {}).get("search", [])[:3]
                results = [
                    {
                        "title": item.get("title"),
                        "snippet": re.sub(r'<[^>]+>', '', item.get("snippet", ""))
                    }
                    for item in raw_items
                ]
                log(DEBUG, f"Wikipedia returned {len(results)} result(s)")
                if results:
                    return json.dumps({"results": results})
                log(WARN, "Wikipedia search returned no results")
                return json.dumps({"error": "No results found."})
        except Exception as e:
            log(ERROR, f"Wikipedia fallback request failed: {e}")
            return json.dumps({"error": str(e)})

    @staticmethod
    def _fallback_fetch(urls, max_chars=3000):
        """Zero-auth raw HTML scraper fallback."""
        if not urls:
            return json.dumps({"error": "No URLs provided"})
        if isinstance(urls, str):
            urls = [urls]

        log(DEBUG, f"HTTP fallback fetch: {len(urls[:3])} URL(s)")
        results = []
        for url in urls[:3]:
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    html = resp.read().decode('utf-8', errors='replace')
                    # Strip all scripts, styles, and HTML tags
                    text = re.sub(
                        r'<style[^>]*>.*?</style>|<script[^>]*>.*?</script>|<[^>]+>', ' ', html, flags=re.DOTALL)
                    clean_text = re.sub(r'\s+', ' ', text).strip()
                    log(DEBUG, f"Fetched {url}: {len(clean_text)} chars")
                    results.append(
                        {"url": url, "text": clean_text[:int(max_chars)]})
            except Exception as e:
                log(ERROR, f"Fetch failed for {url}: {e}")
                results.append({"url": url, "error": str(e)})

        return json.dumps({"results": results})

# ==============================================================================
# PROMPT PRE-PROCESSING
# ==============================================================================


def expand_file_references(prompt):
    """Detects @filepath tokens and injects the file contents.

    Uses a single-pass segment accumulation strategy: matches are processed in
    forward order against the *original* string, so indices never shift.
    Segments are joined once at the end, giving O(N) time and space instead of
    the O(N²) that repeated string slicing/concatenation would produce.
    """
    if not prompt or "@" not in prompt:
        return prompt

    log(DEBUG, f"expand_file_references: prompt length={len(prompt)}")
    matches = list(re.finditer(r'(?:^|\s)(@([^\s]+))', prompt))
    log(DEBUG, f"Found {len(matches)} potential @filepath token(s)")

    if not matches:
        return prompt

    segments = []
    cursor = 0  # tracks our position in the original prompt string

    for match in matches:
        token_start, token_end = match.span(1)
        raw_token = match.group(1)
        clean_path = match.group(2).rstrip('.,;:!?)"\'')
        resolved = os.path.abspath(os.path.expanduser(clean_path))

        log(DEBUG, f"Token '{raw_token}' → resolved={resolved}")

        # Emit everything between the previous token and this one verbatim
        segments.append(prompt[cursor:token_start])

        if os.path.isfile(resolved):
            try:
                size = os.path.getsize(resolved)
                with open(resolved, 'r', encoding='utf-8', errors='replace') as f:
                    raw = f.read(102400)
                truncation_notice = "\n...[Truncated]..." if size > 102400 else ""
                content = raw + truncation_notice

                replacement = (
                    f"@{clean_path}\n\n"
                    f"--- File: {resolved} ---\n"
                    f"{content}\n"
                    f"--- End File ---\n"
                )
                segments.append(replacement)
                print(
                    f"{COLOR_DIM}[Attached: {resolved} ({size} bytes)]{COLOR_RESET}")
                log(DEBUG,
                    f"Attached {resolved}: {size} bytes — total segments: {len(segments)}")
            except Exception as e:
                # keep original token
                segments.append(prompt[token_start:token_end])
                log(ERROR, f"Failed to read @{clean_path}: {e}")
        else:
            # keep original token
            segments.append(prompt[token_start:token_end])
            log(ERROR,
                f"File not found: {resolved} — use absolute paths (e.g., @~/... or @/home/...)")

        cursor = token_end

    segments.append(prompt[cursor:])  # remainder after the last token
    new_prompt = "".join(segments)
    log(DEBUG, f"expand_file_references done: final length={len(new_prompt)}")
    return new_prompt

# ==============================================================================
# STREAM STATE MACHINE & UI
# ==============================================================================


class StreamState(Enum):
    START = auto()
    THOUGHT = auto()
    RESPONSE = auto()


class StreamUI:
    """Isolates the complex FSM terminal formatting from the networking logic."""

    def __init__(self):
        self.state = StreamState.START
        self.reply = ""

    def transition(self, new_state):
        if self.state == new_state:
            return

        if new_state == StreamState.THOUGHT:
            sys.stdout.write(COLOR_DIM)
        elif new_state == StreamState.RESPONSE:
            if self.state == StreamState.THOUGHT:
                sys.stdout.write(SEPARATOR)
            sys.stdout.write(COLOR_RESET)

        sys.stdout.flush()
        self.state = new_state

    def process_chunk(self, text):
        if self.state == StreamState.RESPONSE:
            sys.stdout.write(text)
            self.reply += text
            return

        pos = 0
        for match in RE_TAG.finditer(text):
            start, end = match.span()
            tag = match.group(0).lower()

            before = text[pos:start]
            if before:
                self._write_styled(before)

            pos = end
            if tag in RE_OPEN_TAGS:
                self.transition(StreamState.THOUGHT)
            elif tag in RE_CLOSE_TAGS:
                self.transition(StreamState.RESPONSE)

        after = text[pos:]
        if after:
            self._write_styled(after)

    def _write_styled(self, text):
        if self.state == StreamState.THOUGHT:
            sys.stdout.write(f"{COLOR_DIM}{text}{COLOR_RESET}")
        elif self.state == StreamState.START:
            if text.strip():
                self.transition(StreamState.RESPONSE)
                sys.stdout.write(text)
                self.reply += text
            else:
                sys.stdout.write(text)

# ==============================================================================
# CORE SESSION LOOP
# ==============================================================================


class ChatSession:
    def __init__(self, initial_prompt=None):
        self.url = "http://localhost:8080/v1/chat/completions"
        self.messages = [{"role": "system", "content": self._load_system()}]
        self.pending_tool_response = False

        self.current_prompt = expand_file_references(
            initial_prompt) if initial_prompt else None
        if self.current_prompt:
            print(f"{COLOR_BLUE}You:{COLOR_RESET} {self.current_prompt}")

        log(DEBUG,
            f"Session initialized: system_prompt={len(self.messages[0]['content'])} chars, "
            f"initial_prompt={'set' if self.current_prompt else 'none'}")

    def _load_system(self):
        path = os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "system_prompt.txt")
        log(DEBUG, f"Loading system prompt from {path}")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            log(DEBUG, f"System prompt loaded: {len(content)} chars")
            return content
        log(DEBUG, "system_prompt.txt not found — using built-in default")
        return "You are a deterministic execution engine."

    def _read_input(self):
        log(DEBUG, "Waiting for user input…")
        print(
            f"\n{COLOR_BLUE}You (Paste code or type, press Ctrl+D to submit):{COLOR_RESET}")
        try:
            text = sys.stdin.read()
            if not text:
                raise EOFError
            log(DEBUG, f"Input received: {len(text)} chars (raw)")
            return expand_file_references(text.strip())
        except (KeyboardInterrupt, EOFError):
            sys.stdout.write(f"{COLOR_RESET}\n[Exiting Void...]\n")
            sys.exit(0)

    def _safe_split_tags(self, buf):
        """Prevents bisecting XML reasoning tags across SSE network chunks.

        If a '<' appears after the last '>' and the partial tag is short enough
        to still be growing, hold it back until the next chunk completes it.
        """
        last_open = buf.rfind('<')
        last_close = buf.rfind('>')
        tail = buf[last_open:]
        tag_is_incomplete = last_open != - \
            1 and last_close < last_open and len(tail) <= 35
        if tag_is_incomplete:
            return buf[:last_open], tail
        return buf, ""

    def stream_turn(self):
        """Handles a single network request to the local LLM and streams the result."""
        request_body = {
            "messages": self.messages,
            "stream": True,
            "tools": DEFAULT_TOOLS,
            "chat_template_kwargs": {"enable_thinking": True},
        }

        log(DEBUG,
            f"→ POST {self.url}  (history={len(self.messages)} messages)")
        log(TRACE, f"Request body:\n{json.dumps(request_body, indent=2)}")

        req = urllib.request.Request(
            self.url,
            headers={'Content-Type': 'application/json'},
            data=json.dumps(request_body).encode('utf-8'),
        )

        print(f"\n{COLOR_PURPLE}Orb:{COLOR_RESET}", end=" ", flush=True)

        ui = StreamUI()
        buf = ""
        tools_buf = {}
        using_reasoning_api = False
        tool_header_printed = False  # printed once when the first tool delta arrives
        reasoning_started = False

        try:
            with urllib.request.urlopen(req) as resp:
                for line in resp:
                    line = line.decode('utf-8').strip()
                    if line:
                        log(TRACE, f"SSE: {line}")

                    if not line.startswith("data: ") or line[6:] == "[DONE]":
                        continue

                    try:
                        delta = json.loads(line[6:]).get(
                            'choices', [{}])[0].get('delta', {})
                    except json.JSONDecodeError:
                        log(ERROR, f"Failed to parse SSE line: {line[:80]}")
                        continue

                    # 1. Process Dedicated Reasoning Stream
                    reasoning = delta.get('reasoning_content')
                    if reasoning:
                        if not reasoning_started:
                            log(DEBUG, "Reasoning stream started")
                            reasoning_started = True
                        using_reasoning_api = True
                        ui.transition(StreamState.THOUGHT)
                        sys.stdout.write(reasoning)
                        sys.stdout.flush()

                    # 2. Process Standard Text Content
                    chunk = delta.get('content')
                    if chunk:
                        if using_reasoning_api:
                            log(DEBUG, "Reasoning stream ended; switching to response")
                            ui.transition(StreamState.RESPONSE)
                            using_reasoning_api = False

                        buf += chunk
                        safe_text, buf = self._safe_split_tags(buf)
                        if safe_text:
                            ui.process_chunk(safe_text)
                            sys.stdout.flush()

                    # 3. Buffer JSON Tool Calls
                    for tc in delta.get('tool_calls', []):
                        if not tool_header_printed:
                            log(DEBUG,
                                "[Tool Call] — buffering tool call delta(s)")
                            tool_header_printed = True

                        idx = tc.get('index', 0)
                        if idx not in tools_buf:
                            tools_buf[idx] = {
                                "id": tc.get("id", ""),
                                "type": "function",
                                "name": "",
                                "arguments": "",
                            }

                        fn = tc.get("function", {})
                        if fn.get("name"):
                            tools_buf[idx]["name"] = fn["name"]
                            log(DEBUG, f"Tool #{idx} name: {fn['name']}")
                        if fn.get("arguments"):
                            tools_buf[idx]["arguments"] += fn["arguments"]

                if buf:
                    ui.process_chunk(buf)
                ui.transition(StreamState.RESPONSE)

                log(DEBUG,
                    f"Stream complete: reply={len(ui.reply)} chars, tool_calls={len(tools_buf)}")

        except KeyboardInterrupt:
            sys.stdout.write(f"{COLOR_RESET}\n[Generation Interrupted]\n")
        except Exception as e:
            log(ERROR, f"Connection error: {e}")
            if self.messages[-1]["role"] == "user":
                self.messages.pop()
            return

        if tools_buf:
            log(TRACE, f"tools_buf:\n{json.dumps(tools_buf, indent=2)}")

        self._finalize_turn(ui.reply, tools_buf)

    def _finalize_turn(self, reply, tools_buf):
        """Saves message history and executes any tools the LLM requested."""
        log(DEBUG,
            f"Finalizing turn: reply={len(reply)} chars, {len(tools_buf)} tool call(s)")
        msg = {"role": "assistant", "content": reply or None}

        if tools_buf:
            # Resolve call IDs once so they are consistent in both the assistant
            # message and the corresponding tool-result messages.
            resolved = {i: (t["id"] or f"call_{i}")
                        for i, t in tools_buf.items()}

            msg["tool_calls"] = [
                {
                    "id": resolved[i],
                    "type": t["type"],
                    "function": {"name": t["name"], "arguments": t["arguments"]},
                }
                for i, t in tools_buf.items()
            ]
            self.messages.append(msg)
            log(DEBUG,
                f"Appended assistant message with {len(msg['tool_calls'])} tool_call(s)")

            for i, t in tools_buf.items():
                call_id = resolved[i]
                log(DEBUG, f"↳ {t['name']}  args: {t['arguments']}")
                log(DEBUG,
                    f"Executing tool #{i}: {t['name']} (call_id={call_id})")
                result = ToolManager.execute(t["name"], t["arguments"])

                # Show a truncated preview so the user can see what came back
                preview = result[:300] if result else "(empty)"
                rest = result[300:] if result else "(empty)"
                log(DEBUG, f"  ↩ {preview}")
                log(TRACE, rest)
                log(DEBUG,
                    f"Tool #{i} result: {len(result) if result else 0} chars total")

                self.messages.append(
                    {"role": "tool", "tool_call_id": call_id, "content": result}
                )
                log(DEBUG, f"Appended tool result for call_id={call_id}")

            log(DEBUG, "pending_tool_response=True — will re-query LLM")
            self.pending_tool_response = True
        else:
            if reply:
                self.messages.append(msg)
                log(DEBUG, "Appended assistant message to history")
            else:
                log(WARN, "Empty reply and no tool calls — nothing appended")
            log(DEBUG, "pending_tool_response=False — turn complete")
            self.pending_tool_response = False
        print()

    EXIT_COMMANDS = frozenset({"exit", "quit"})

    def run(self):
        print(
            f"{COLOR_DIM}[ Chat Activated. Paste code or type, press Ctrl+D to submit ]{COLOR_RESET}\n")
        while True:
            log(DEBUG,
                f"Loop: current_prompt={'set' if self.current_prompt else 'none'}, "
                f"pending_tool_response={self.pending_tool_response}")

            if not self.current_prompt and not self.pending_tool_response:
                self.current_prompt = self._read_input()
                if not self.current_prompt:
                    continue
                if self.current_prompt.lower() in self.EXIT_COMMANDS:
                    log(DEBUG, "Exit command received — shutting down")
                    break

            if self.current_prompt:
                log(DEBUG,
                    f"Appending user message: {len(self.current_prompt)} chars")
                self.messages.append(
                    {"role": "user", "content": self.current_prompt})
                self.current_prompt = None

            self.stream_turn()


if __name__ == "__main__":
    try:
        raw_prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
        ChatSession(raw_prompt if raw_prompt.strip() else None).run()
    except Exception as e:
        log(ERROR, f"CRITICAL CRASH: {e}")
        traceback.print_exc()
        input("\nPress Enter to close this window...")
