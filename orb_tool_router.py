#!/usr/bin/env python3
"""
OrbToolRouter - Hybrid MCP Client Manager

Acts as a hybrid MCP client manager using the official `mcp` SDK to connect to multiple
MCP servers over streamable-http or SSE transports, aggregate their tools into OpenAI function
schemas, and route tool call execution requests.
"""

import json
import asyncio
import logging
from typing import List, Dict, Any, Union, Optional
from contextlib import AsyncExitStack

logger = logging.getLogger("orb.mcp")

# Imports from official mcp SDK (with dynamic check for graceful import handling)
try:
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamable_http_client
    HAS_MCP_SDK = True
except ImportError:
    HAS_MCP_SDK = False


class OrbToolRouter:
    """
    Hybrid MCP Client Manager.

    Responsibilities:
    1. Config: Parses server configurations (id, type: 'streamable-http' | 'sse', url).
    2. Lifecycle Management: Uses contextlib.AsyncExitStack for multiple concurrent connection lifecycles.
    3. Hybrid Transports: Dynamically instantiates streamablehttp_client or sse_client.
    4. Discovery: Wraps streams in ClientSession, calls initialize(), and lists tools.
    5. Aggregation: Converts discovered tools into OpenAI function schemas and maintains master list.
    6. Execution Routing: Maps tool_name -> active ClientSession and executes calls.
    7. Cleanup: Provides shutdown() method to cleanly close AsyncExitStack.
    """

    def __init__(self, config_source: Union[str, List[Dict[str, Any]]]):
        """
        Initialize the router with a JSON file path, JSON string, or list of server dictionaries.
        """
        if isinstance(config_source, str):
            stripped = config_source.strip()
            if stripped.startswith("[") or stripped.startswith("{"):
                self.config = json.loads(stripped)
            else:
                with open(config_source, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
        elif isinstance(config_source, (list, dict)):
            self.config = config_source
        else:
            raise TypeError(
                "config_source must be a file path, JSON string, list of dicts, or dict.")

        if isinstance(self.config, dict) and "mcpServers" in self.config:
            parsed_servers = []
            for k, v in self.config["mcpServers"].items():
                v["id"] = k
                if "command" in v:
                    v["type"] = "stdio"
                parsed_servers.append(v)
            self.config = parsed_servers
        elif isinstance(self.config, dict):
            self.config = [self.config]

        self._exit_stack: Optional[AsyncExitStack] = None
        self._routes: Dict[str, Any] = {}  # tool_name -> active ClientSession
        # Aggregated OpenAI function schemas
        self.tools: List[Dict[str, Any]] = []

    async def initialize(self) -> List[Dict[str, Any]]:
        """
        Connects to all configured MCP servers using AsyncExitStack, initializes sessions,
        discovers available tools, and builds the OpenAI function schema list and routing table.
        """
        logger.debug("Initializing OrbToolRouter")
        if not HAS_MCP_SDK:
            logger.error("The official 'mcp' SDK is not installed")
            raise RuntimeError(
                "The official 'mcp' SDK is not installed. Install it with: pip install mcp")

        self._exit_stack = AsyncExitStack()
        await self._exit_stack.__aenter__()

        for server in self.config:
            server_id = server.get("id", "unknown")
            server_type = server.get("type")
            url = server.get("url")

            if not server_type or (not url and server_type != "stdio"):
                continue

            logger.debug(
                f"Connecting to MCP server '{server_id}' at {url} via {server_type}")
            try:
                # 3. Dynamic Transport Selection
                if server_type == "streamable-http":
                    transport = streamable_http_client(url)
                elif server_type == "sse":
                    transport = sse_client(url)
                elif server_type == "stdio":
                    from mcp.client.stdio import stdio_client, StdioServerParameters, get_default_environment
                    command = server.get("command")
                    args = server.get("args", [])
                    env_override = server.get("env", None)
                    env = get_default_environment()
                    if env_override:
                        env.update(env_override)
                    transport = stdio_client(StdioServerParameters(
                        command=command, args=args, env=env))
                else:
                    raise ValueError(
                        f"Unsupported transport type '{server_type}' for server '{server_id}'")

                # 2 & 4. Connection & Session Lifecycle with AsyncExitStack
                read_stream, write_stream = await self._exit_stack.enter_async_context(transport)
                session = await self._exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )

                # Initialize MCP Session handshake
                await session.initialize()
                logger.debug(f"Initialized MCP Session for '{server_id}'")

                # Tool discovery
                tools_response = await session.list_tools()
                for tool in tools_response.tools:
                    # 5. Schema Aggregation (Convert to standard OpenAI function format)
                    schema = getattr(tool, "inputSchema",
                                     getattr(tool, "input_schema", {}))
                    parameters = (
                        schema.model_dump()
                        if hasattr(schema, "model_dump")
                        else schema if isinstance(schema, dict) else {}
                    )

                    openai_schema = {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description or "",
                            "parameters": parameters,
                        },
                    }

                    self.tools.append(openai_schema)

                    # 6. Execution Routing Table (tool_name -> active_session)
                    self._routes[tool.name] = session
            except Exception as e:
                logger.error(
                    f"Failed to initialize MCP server '{server_id}': {e}")
                continue

        return self.tools

    async def execute_tool(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """
        Looks up the active session for tool_name and executes the tool via session.call_tool().
        """
        session = self._routes.get(tool_name)
        if not session:
            logger.error(
                f"Tool '{tool_name}' not found in active MCP routing table.")
            raise KeyError(
                f"Tool '{tool_name}' not found in active MCP routing table.")

        arguments = arguments or {}
        logger.debug(
            f"Executing tool '{tool_name}' with arguments: {arguments}")
        try:
            result = await session.call_tool(name=tool_name, arguments=arguments)
            logger.debug(f"Tool '{tool_name}' executed successfully")
            return result
        except Exception as e:
            logger.error(f"Execution of tool '{tool_name}' failed: {e}")
            raise

    async def shutdown(self):
        """
        Cleanly closes all active sessions and underlying transports by closing AsyncExitStack.
        """
        if self._exit_stack:
            logger.debug("Shutting down active MCP sessions")
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._routes.clear()
            self.tools.clear()
            logger.debug("MCP shutdown complete")

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.shutdown()


# Standalone runner for quick testing and demonstration
if __name__ == "__main__":
    sample_config = [
        {
            "id": "exa-search",
            "type": "streamable-http",
            "url": "https://mcp.exa.ai/mcp"
        },
        {
            "id": "gallery-mcp",
            "type": "streamable-http",
            "url": "http://127.0.0.1:8000/mcp"
        }
    ]

    async def main():
        logging.basicConfig(level=logging.DEBUG)
        logger.info("Initializing OrbToolRouter with sample config...")
        router = OrbToolRouter(sample_config)
        try:
            tools = await router.initialize()
            logger.info(
                f"Discovered {len(tools)} tools:\n{json.dumps(tools, indent=2)}")
        except Exception as err:
            logger.error(f"Initialization result: {err}")
        finally:
            await router.shutdown()

    asyncio.run(main())
