"""A synchronous handle on an MCP server spoken to over stdio.

`grade_suite` calls `triager.triage(ticket)` synchronously, 26 times. The MCP
client API is entirely async. Bridging those two facts is this module's whole
job, and it does it by owning a background thread with its own event loop and
keeping ONE server process alive for the life of the triager.

Why one process rather than one per ticket: spawning `uv run python -m
msp_tools.server` costs about a second, and a per-ticket spawn would add half a
minute to every suite run and make the server's in-memory state (confirmation
tokens, the ticket store) reset underneath the agent between tickets. The
server is designed to be long-lived; a client that treats it otherwise would be
measuring a system nobody runs.
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPServerError(RuntimeError):
    """The server could not be started or spoke unintelligibly."""


def _root_cause(exc: BaseException, depth: int = 0) -> str:
    """The innermost useful message from a possibly-nested ExceptionGroup.

    anyio wraps startup failures in a TaskGroup, so the exception a caller sees
    is 'unhandled errors in a TaskGroup (1 sub-exception)' - true, and useless.
    The real message ('No such file or directory: uv', a proxy refusing a TLS
    handshake, a Python toolchain that could not be fetched) is one or two
    levels down. Anyone hitting this is already confused; handing them the
    wrapper costs an hour.
    """
    if depth > 6:
        return f"{type(exc).__name__}: {exc}"
    inner = getattr(exc, "exceptions", None)
    if inner:
        return " | ".join(_root_cause(e, depth + 1) for e in inner)
    if exc.__cause__ is not None:
        return _root_cause(exc.__cause__, depth + 1)
    return f"{type(exc).__name__}: {exc}"


class MCPBridge:
    """Start an MCP server over stdio and call its tools from sync code."""

    def __init__(
        self,
        server_dir: Path,
        command: str | None = None,
        env: dict[str, str] | None = None,
        startup_timeout: float = 60.0,
    ) -> None:
        self._server_dir = Path(server_dir).resolve()
        if not self._server_dir.exists():
            raise MCPServerError(f"server directory does not exist: {self._server_dir}")

        # uv, by absolute path if given. Claude Desktop needs the absolute path
        # because it does not inherit a shell PATH; a subprocess launched from
        # here does inherit ours, so bare "uv" normally resolves. MSP_TOOLS_UV
        # is the escape hatch for when it does not.
        self._command = command or os.environ.get("MSP_TOOLS_UV") or "uv"

        # --no-sync so launching does not re-resolve dependencies, which needs
        # network and fails behind a TLS-intercepting proxy. Same reasoning as
        # the Claude Desktop config in msp-tools-mcp's README.
        self._args = [
            "--directory", str(self._server_dir),
            "run", "--no-sync",
            "python", "-m", "msp_tools.server",
        ]
        self._env = env
        self._startup_timeout = startup_timeout

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._error: BaseException | None = None
        self._session: ClientSession | None = None
        self.tools: list[Any] = []

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run_loop, name="mcp-bridge", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(self._startup_timeout):
            raise MCPServerError(
                f"MCP server did not become ready within {self._startup_timeout}s.\n"
                f"  command: {self._command} {' '.join(self._args)}\n"
                "  Try running that by hand; a server that fails to start "
                "usually prints the reason to stderr."
            )
        if self._error is not None:
            raise MCPServerError(
                f"MCP server failed to start.\n"
                f"  command: {self._command} {' '.join(self._args)}\n"
                f"  cause:   {_root_cause(self._error)}"
            ) from self._error

    def close(self) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._stop_event_set)
        if self._thread is not None:
            self._thread.join(timeout=15)
        self._thread = None

    def _stop_event_set(self) -> None:
        self._stop.set()
        self._stop_async.set()

    def __enter__(self) -> MCPBridge:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- the background loop ----------------------------------------------

    def _run_loop(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as e:  # noqa: BLE001 - reported to the caller
            self._error = e
            self._ready.set()

    async def _serve(self) -> None:
        self._stop_async = asyncio.Event()
        params = StdioServerParameters(
            command=self._command, args=self._args, env=self._env
        )
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    self._loop = asyncio.get_running_loop()
                    self.tools = list((await session.list_tools()).tools)
                    self._ready.set()
                    await self._stop_async.wait()
        except BaseException as e:  # noqa: BLE001
            self._error = e
            self._ready.set()
            raise

    # -- calling -----------------------------------------------------------

    def call_tool(self, name: str, arguments: dict[str, Any], timeout: float = 120.0) -> dict:
        """Call a tool and return its payload as a plain dict.

        Prefers `structuredContent`, which FastMCP populates because the server
        returns Pydantic models. Falls back to parsing the text block, because
        a caller that only understood one of the two would break silently the
        first time a tool returned the other.
        """
        if self._session is None or self._loop is None:
            raise MCPServerError("bridge is not started")

        fut = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(name, arguments), self._loop
        )
        result = fut.result(timeout=timeout)

        if result.structuredContent is not None:
            return result.structuredContent

        import json

        for block in result.content or []:
            text = getattr(block, "text", None)
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"ok": False, "error_code": "UNPARSEABLE", "raw": text}
        return {"ok": False, "error_code": "EMPTY_RESULT"}

    def anthropic_tool_specs(self) -> list[dict]:
        """The server's tools in the shape the Anthropic Messages API wants.

        The descriptions are passed through unchanged and deliberately. They
        are the design artefact msp-tools-mcp treats them as - each states what
        the tool does, what it does NOT do, when to prefer a sibling, and what
        its errors mean. Rewriting them here would mean the agent is reading a
        summary of the contract rather than the contract.
        """
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.inputSchema,
            }
            for t in self.tools
        ]
