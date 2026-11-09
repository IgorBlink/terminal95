"""Small, asynchronous client for the installed Codex CLI's stdio app server.

Authentication and session persistence remain owned by Codex. This module never
reads credential files, shells out user messages, or opens a listening socket.
"""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
import json
import os
from pathlib import Path
import shutil
import signal
import sys
from typing import Any


class ProtocolError(RuntimeError):
    """An actionable Codex transport or JSON-RPC error."""


def find_codex() -> str:
    """Find a CLI in PATH, with the macOS desktop bundle as a fallback."""
    override = os.environ.get("CODEX95_CODEX_BINARY")
    if override:
        path = Path(override).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise ProtocolError("CODEX95_CODEX_BINARY does not point to an executable Codex CLI.")
    executable = shutil.which("codex")
    if executable:
        return executable
    candidates = (
        "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
        "/Applications/Codex.app/Contents/Resources/codex",
        str(Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"),
        "/opt/homebrew/bin/codex",
        "/usr/local/bin/codex",
    )
    for candidate in candidates:
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise ProtocolError("Codex CLI was not found. Install Codex, run `codex login`, and reopen Codex 95.")


class CodexClient:
    """Own one app-server child process and route its JSONL messages.

    Public methods return thread/turn objects (not RPC envelopes). Notifications
    are placed unmodified in ``events``. Client-only notifications use the
    ``codex95/`` prefix. ``close`` is idempotent and terminates only the process
    group created by this client.
    """

    def __init__(
        self,
        cwd: str | None = None,
        model: str | None = None,
        sandbox: str = "read-only",
        *,
        executable: str | None = None,
        rpc_timeout: float = 60.0,
    ) -> None:
        if sandbox not in {"read-only", "workspace-write"}:
            raise ValueError("Codex 95 supports read-only or workspace-write sandbox modes.")
        self.cwd = str(Path(cwd or os.getcwd()).expanduser().resolve())
        self.model = model
        self.requested_model = model
        self.sandbox = sandbox
        self.executable = executable
        self.rpc_timeout = rpc_timeout
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.stderr_tail: deque[str] = deque(maxlen=40)
        self.process: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._next_id = 1
        self._write_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._reader: asyncio.Task[None] | None = None
        self._stderr_reader: asyncio.Task[None] | None = None
        self._ready = False
        self._closing = False
        self._terminal_error: ProtocolError | None = None

    async def start(self) -> None:
        async with self._start_lock:
            if self._ready:
                return
            if self._closing:
                raise ProtocolError("The Codex connection is closed. Reopen Codex 95 to reconnect.")
            if not Path(self.cwd).is_dir():
                raise ProtocolError(f"Working folder does not exist: {self.cwd}")
            binary = self.executable or find_codex()
            try:
                self.process = await asyncio.create_subprocess_exec(
                    binary,
                    "app-server",
                    "--listen",
                    "stdio://",
                    "-c",
                    'ask_for_approval="never"',
                    "-c",
                    f'sandbox_mode="{self.sandbox}"',
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.cwd,
                    start_new_session=sys.platform != "win32",
                    limit=32 * 1024 * 1024,
                )
                self._reader = asyncio.create_task(self._read_messages(), name="codex95-jsonl")
                self._stderr_reader = asyncio.create_task(self._read_stderr(), name="codex95-stderr")
                await self._rpc(
                    "initialize",
                    {"clientInfo": {"name": "codex95", "title": "Codex 95", "version": "1.0.0"}},
                )
                await self._write({"method": "initialized", "params": {}})
                self._ready = True
            except BaseException:
                await self.close()
                raise

    async def list_threads(self, limit: int = 30) -> list[dict[str, Any]]:
        """List recent user sessions, including sessions created by this app."""
        result = await self._rpc(
            "thread/list",
            {
                "limit": max(1, min(limit, 100)),
                "sortKey": "updated_at",
                "sortDirection": "desc",
                "sourceKinds": ["cli", "vscode", "appServer", "exec", "unknown"],
                "modelProviders": [],
                "archived": False,
            },
        )
        return result.get("data", [])

    def _thread_params(self, cwd: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"approvalPolicy": "never", "sandbox": self.sandbox}
        if cwd is not None:
            folder = str(Path(cwd).expanduser().resolve())
            if not Path(folder).is_dir():
                raise ProtocolError(f"Working folder does not exist: {folder}")
            params["cwd"] = folder
        if self.requested_model:
            params["model"] = self.requested_model
        return params

    def _thread_result(self, result: dict[str, Any]) -> dict[str, Any]:
        thread = dict(result["thread"])
        for key in ("model", "cwd", "sandbox", "reasoningEffort", "modelProvider"):
            if key in result:
                thread[key] = result[key]
        self.model = result.get("model", self.model)
        return thread

    async def new_thread(self, cwd: str | None = None) -> dict[str, Any]:
        result = await self._rpc("thread/start", self._thread_params(cwd or self.cwd))
        return self._thread_result(result)

    async def resume_thread(
        self, thread_id: str, cwd: str | None = None, *, include_history: bool = True
    ) -> dict[str, Any]:
        params = self._thread_params(cwd)
        params.update({"threadId": thread_id, "excludeTurns": True})
        result = await self._rpc("thread/resume", params)
        thread = self._thread_result(result)
        if not include_history:
            # The server still resumes its persisted conversation. Only skip
            # downloading the transcript that the UI would otherwise replay.
            thread["turns"] = []
            return thread
        # Pagination avoids deprecated full-history resume hydration and works
        # with both legacy and the current paginated session store.
        turns: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page_params: dict[str, Any] = {
                "threadId": thread_id,
                "limit": 50,
                "sortDirection": "desc",
                "itemsView": "full",
            }
            if cursor:
                page_params["cursor"] = cursor
            page = await self._rpc("thread/turns/list", page_params)
            turns.extend(page.get("data", []))
            cursor = page.get("nextCursor")
            if not cursor or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
        thread["turns"] = list(reversed(turns))
        return thread

    async def send_turn(self, thread_id: str, text: str) -> dict[str, Any]:
        if not text.strip():
            raise ProtocolError("Write a message before sending.")
        result = await self._rpc(
            "turn/start",
            {"threadId": thread_id, "input": [{"type": "text", "text": text}]},
        )
        return result["turn"]

    async def interrupt(self, thread_id: str, turn_id: str) -> None:
        await self._rpc("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})

    async def _write(self, message: dict[str, Any]) -> None:
        if self._terminal_error:
            raise self._terminal_error
        process = self.process
        if process is None or process.stdin is None or process.returncode is not None:
            raise ProtocolError("Codex is not connected. Reopen Codex 95 to reconnect.")
        data = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            async with self._write_lock:
                process.stdin.write(data)
                await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise ProtocolError("The Codex connection ended. Reopen Codex 95 to reconnect.") from exc

    async def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        if self._closing:
            raise ProtocolError("The Codex connection is closed.")
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout=self.rpc_timeout)
        except asyncio.TimeoutError as exc:
            raise ProtocolError(f"Codex did not answer {method} within {self.rpc_timeout:g}s. Check your connection and retry.") from exc
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    def _fail_pending(self, error: ProtocolError) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)

    async def _read_messages(self) -> None:
        assert self.process and self.process.stdout
        try:
            while line := await self.process.stdout.readline():
                try:
                    message = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ProtocolError("Codex returned an invalid JSON message. Update Codex CLI and reopen Codex 95.") from exc
                if not isinstance(message, dict):
                    continue
                if "method" in message:
                    if "id" in message:
                        await self._handle_server_request(message)
                    else:
                        await self.events.put(message)
                elif "id" in message:
                    future = self._pending.get(message["id"])
                    if future is None or future.done():
                        continue
                    if "error" in message:
                        error = message["error"]
                        detail = error.get("message", str(error)) if isinstance(error, dict) else str(error)
                        future.set_exception(ProtocolError(detail))
                    else:
                        future.set_result(message.get("result", {}))
            if not self._closing:
                raise ProtocolError("Codex disconnected. Run `codex login status`, then reopen Codex 95.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = exc if isinstance(exc, ProtocolError) else ProtocolError(f"Codex connection failed: {exc}")
            self._terminal_error = error
            self._fail_pending(error)
            if not self._closing:
                await self.events.put({"method": "codex95/disconnected", "params": {"message": str(error)}})

    async def _handle_server_request(self, message: dict[str, Any]) -> None:
        # The simple chat has no approval dialogs. Explicitly decline rather
        # than silently granting access or letting a server request hang.
        method = message["method"]
        response: dict[str, Any] = {"id": message["id"]}
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            response["result"] = {"decision": "decline"}
        elif method == "item/permissions/requestApproval":
            response["result"] = {"permissions": {}, "scope": "turn"}
        elif method in {"applyPatchApproval", "execCommandApproval"}:
            response["result"] = {"decision": "abort"}
        elif method == "mcpServer/elicitation/request":
            response["result"] = {"action": "decline"}
        elif method == "item/tool/requestUserInput":
            response["result"] = {"answers": {}}
        else:
            response["error"] = {"code": -32601, "message": f"Codex 95 does not support client request {method}."}
        await self._write(response)
        await self.events.put({
            "method": "codex95/requestDeclined",
            "params": {"requestMethod": method, **message.get("params", {})},
        })

    async def _read_stderr(self) -> None:
        assert self.process and self.process.stderr
        while chunk := await self.process.stderr.read(4096):
            for line in chunk.decode("utf-8", errors="replace").splitlines():
                self.stderr_tail.append(line[-2000:])

    async def close(self) -> None:
        async with self._close_lock:
            if self._closing:
                return
            self._closing = True
            self._ready = False
            self._fail_pending(ProtocolError("The Codex connection was closed."))
            process = self.process
            if process is not None:
                if process.stdin is not None:
                    process.stdin.close()
                if process.returncode is None:
                    with suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(process.wait(), timeout=2.0)
                # Kill the private process group even if the parent already
                # exited: model tool and MCP children must not be orphaned.
                if sys.platform != "win32":
                    with suppress(ProcessLookupError, PermissionError):
                        os.killpg(process.pid, signal.SIGTERM)
                elif process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.terminate()
                if process.returncode is None:
                    with suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(process.wait(), timeout=2.0)
                if sys.platform != "win32":
                    with suppress(ProcessLookupError, PermissionError):
                        os.killpg(process.pid, signal.SIGKILL)
                elif process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.kill()
                if process.returncode is None:
                    await process.wait()
            for task in (self._reader, self._stderr_reader):
                if task is not None and task is not asyncio.current_task():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

    async def __aenter__(self) -> CodexClient:
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()
