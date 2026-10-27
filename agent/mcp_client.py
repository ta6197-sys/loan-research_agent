"""금융 도구 전용 MCP subprocess 연결."""
import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from agent.finance.tool_policy import checked_finance_arguments

FINANCE_SERVER = Path(__file__).resolve().parent.parent / "mcp_server_finance.py"

class MCPClient:
    def __init__(self, server_scripts: dict[str, Path] | None = None,
                 server_env: dict[str, dict[str, str]] | None = None):
        self._server_scripts = server_scripts or {"finance": FINANCE_SERVER}
        if set(self._server_scripts) != {"finance"}:
            raise ValueError("finance_server_only")
        self._server_env = server_env or {}
        self._stack: AsyncExitStack | None = None
        self.sessions: dict[str, ClientSession] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._stack = AsyncExitStack()
        try:
            env = {key: os.environ[key] for key in (
                "PATH", "HOME", "SYSTEMROOT", "WINDIR", "TMPDIR", "LANG",
                "LC_ALL", "VIRTUAL_ENV", "PYTHONPATH") if key in os.environ}
            env.update({"FASTMCP_LOG_LEVEL": "ERROR", "LANGCHAIN_TRACING_V2": "false",
                        "LANGSMITH_TRACING": "false"})
            env.update(self._server_env.get("finance", {}))
            params = StdioServerParameters(command=sys.executable,
                                           args=[str(self._server_scripts["finance"])], env=env)
            read, write = await self._stack.enter_async_context(stdio_client(params))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self.sessions["finance"] = session
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self.sessions = {}

    async def call_finance(self, name: str, arguments: dict) -> dict:
        """금융 도구에는 학습용 실패 시뮬레이션/외부 추적 데코레이터를 적용하지 않는다."""
        try:
            arguments = checked_finance_arguments(name, arguments)
        except ValueError as error:
            return {'status': str(error), 'retryable': False}
        async with self._lock:
            result = await self.sessions['finance'].call_tool(name, arguments, read_timeout_seconds=1800)
        if getattr(result, 'is_error', getattr(result, 'isError', False)):
            return {'status':'tool_error','retryable':False}
        try:
            structured = getattr(result,'structured_content',getattr(result,'structuredContent',None))
            if structured:
                return structured
            return json.loads(result.content[0].text)
        except (ValueError, IndexError, AttributeError):
            return {'status':'invalid_tool_response','retryable':False}
