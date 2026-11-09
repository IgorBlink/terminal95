"""A native terminal conversation: a prompt, an answer, the next prompt."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.lexers import SimpleLexer
from prompt_toolkit.styles import Style

from .backend import CodexClient, ProtocolError
from .state import State


def create_prompt(*, input=None, output=None):
    # A single inline prompt needs no cursor-position negotiation. Avoid the
    # fallback screen padding used by terminals that cannot answer CPR queries.
    if output is None:
        os.environ.setdefault("PROMPT_TOOLKIT_NO_CPR", "1")
    keys = KeyBindings()

    @keys.add("enter")
    def submit(event):
        event.current_buffer.validate_and_handle()

    @keys.add("c-j")
    def newline(event):
        event.current_buffer.insert_text("\n")

    return PromptSession(
        [("class:prompt", "> ")],
        input=input,
        output=output,
        history=InMemoryHistory(),
        key_bindings=keys,
        style=Style.from_dict({"": "bg:#aaaaaa #000000", "input": "bg:#aaaaaa #000000", "prompt": "bg:#aaaaaa #000000"}),
        lexer=SimpleLexer("class:input"),
        multiline=False,
        prompt_continuation="  ",
        reserve_space_for_menu=0,
        mouse_support=False,
        enable_suspend=True,
        include_default_pygments_style=False,
    )


def safe_text(text):
    # Model output is text, never terminal control sequences.
    return "".join(char for char in str(text) if char.isprintable() or char in "\n\t")


class NativeWriter:
    GRAY = "\x1b[38;2;0;0;0m\x1b[48;2;204;204;204m"
    USER_GRAY = "\x1b[38;2;0;0;0m\x1b[48;2;170;170;170m"
    THINK_INTERVAL = 0.35

    def __init__(self, stream=None):
        self.stream = stream or sys.stdout
        self.color = bool(self.stream.isatty() and "NO_COLOR" not in os.environ)
        self.open = False
        self._thinking_task = None
        self._thinking_visible = False

    def _print(self, text):
        self.stream.write(text)
        self.stream.flush()

    def begin_answer(self, *, user=False):
        if self.open:
            self.end_answer()
        self.open = True
        shade = self.USER_GRAY if user else self.GRAY
        self._print((shade if self.color else "") + "> ")

    async def start_thinking(self):
        await self.stop_thinking()
        if not self.stream.isatty():
            return
        if not self.open:
            self.begin_answer()
        self._thinking_visible = True
        self._print("\x1b[?25l")
        self._draw_thinking(".  ")
        self._thinking_task = asyncio.create_task(self._animate_thinking(), name="codex95-thinking")

    def _draw_thinking(self, dots):
        if self._thinking_visible and self.open:
            shade = self.GRAY if self.color else ""
            muted = "\x1b[38;2;96;96;96m" if self.color else ""
            self._print(f"\r{shade}> {muted}{dots}\x1b[K")

    async def _animate_thinking(self):
        frames = (".. ", "...", ".  ")
        index = 0
        while True:
            await asyncio.sleep(self.THINK_INTERVAL)
            self._draw_thinking(frames[index % len(frames)])
            index += 1

    def _clear_thinking(self):
        if self._thinking_visible:
            self._thinking_visible = False
            shade = self.GRAY if self.color else ""
            self._print(f"\r{shade}> \x1b[K\x1b[?25h")

    async def stop_thinking(self):
        task, self._thinking_task = self._thinking_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._clear_thinking()

    def write(self, text):
        if not self.open:
            self.begin_answer()
        value = safe_text(text)
        continuation = "\x1b[K\n  " if self.color else "\n  "
        self._print(value.replace("\n", continuation))

    def end_answer(self):
        if self._thinking_task is not None:
            self._thinking_task.cancel()
        self._clear_thinking()
        if self.open:
            self._print(("\x1b[K\x1b[0m" if self.color else "") + "\n\n")
            self.open = False

    def message(self, role, text):
        self.begin_answer(user=role == "user")
        self.write(text)
        self.end_answer()

    def notice(self, text):
        self.end_answer()
        self.message("system", text)


class TerminalChat:
    def __init__(self, cwd, *, client=None, client_factory=None, state=None,
                 session=None, writer=None, prompt=None, demo=False):
        self.cwd = str(Path(cwd).resolve())
        if demo and client is None and client_factory is None:
            from .demo import DemoClient
            client_factory = DemoClient
        self.client_factory = client_factory or (type(client) if client is not None else CodexClient)
        self.client = client or self.client_factory()
        self.state = state or State()
        self.writer = writer or NativeWriter()
        self.prompt = prompt
        self.demo = demo
        self.thread_id = session or (None if demo else self.state.last_session(self.cwd))
        self.turn_id = None
        self.last_answer = ""
        self.busy = False
        self.closed = False
        self._stop_requested = False
        self._interrupt_sent = False
        self._sessions = []

    async def start(self):
        await self.client.start()
        if self.demo:
            self.writer.notice("Demo · offline")
        if self.thread_id:
            try:
                await self.resume(self.thread_id, show_history=False)
            except Exception as exc:
                self.thread_id = None
                self.writer.notice(str(exc))

    def _remember(self):
        if not self.demo:
            try:
                self.state.remember(self.cwd, self.thread_id)
            except OSError:
                pass

    async def resume(self, thread_id, *, show_history=True):
        thread = await self.client.resume_thread(thread_id, include_history=show_history)
        self.thread_id = thread["id"]
        self.cwd = thread.get("cwd") or self.cwd
        self.last_answer = ""
        for turn in thread.get("turns", []) if show_history else []:
            for item in turn.get("items", []):
                kind = item.get("type")
                if kind == "userMessage":
                    text = "\n".join(part.get("text", "") for part in item.get("content", [])
                                     if part.get("type") == "text")
                    if text:
                        self.writer.message("user", text)
                elif kind == "agentMessage" and item.get("text"):
                    self.writer.message("assistant", item["text"])
                    self.last_answer = item["text"]
        self._remember()

    async def ask(self, text):
        if not text.strip():
            return ""
        self.busy = True
        self._stop_requested = self._interrupt_sent = False
        self.turn_id = None
        self.last_answer = ""
        streamed = {}
        displayed_item = None
        try:
            self.writer.begin_answer()
            await self.writer.start_thinking()
            if self.thread_id is None:
                thread = await self.client.new_thread(self.cwd)
                self.thread_id = thread["id"]
                self._remember()
            response = await self.client.send_turn(self.thread_id, text)
            self.turn_id = response.get("turn", response)["id"]
            if self._stop_requested:
                await self.interrupt()
            while True:
                event = await self.client.events.get()
                method, params = event.get("method", ""), event.get("params", {})
                if method == "codex95/disconnected":
                    raise ProtocolError(params.get("message") or "Codex disconnected.")
                if params.get("threadId") and params["threadId"] != self.thread_id:
                    continue
                event_turn = params.get("turnId") or params.get("turn", {}).get("id")
                if event_turn and event_turn != self.turn_id:
                    continue
                if method == "item/agentMessage/delta":
                    delta = params.get("delta", "")
                    if not delta:
                        continue
                    await self.writer.stop_thinking()
                    item_id = params["itemId"]
                    if displayed_item is not None and item_id != displayed_item:
                        self.writer.end_answer()
                        self.writer.begin_answer()
                    displayed_item = item_id
                    streamed[item_id] = streamed.get(item_id, "") + delta
                    self.writer.write(delta)
                    self.last_answer = streamed[item_id]
                elif method == "item/completed" and params.get("item", {}).get("type") == "agentMessage":
                    item = params["item"]
                    item_id, final = item["id"], item.get("text", "")
                    if final:
                        await self.writer.stop_thinking()
                    previous = streamed.get(item_id, "")
                    if displayed_item is not None and item_id != displayed_item:
                        self.writer.end_answer()
                        self.writer.begin_answer()
                    displayed_item = item_id
                    if final.startswith(previous):
                        self.writer.write(final[len(previous):])
                    elif final.strip() != previous.strip():
                        # An unusual server correction gets its own plain reply.
                        self.writer.end_answer()
                        self.writer.begin_answer()
                        self.writer.write(final)
                    streamed[item_id] = final
                    self.last_answer = final
                elif method == "turn/completed":
                    turn = params.get("turn", {})
                    if turn.get("status") == "failed":
                        error = turn.get("error") or {}
                        raise ProtocolError(error.get("message", str(error)) if isinstance(error, dict) else str(error))
                    return self.last_answer
                elif method == "codex95/requestDeclined":
                    await self.writer.stop_thinking()
                    self.writer.notice("That action needs permission and is unavailable in this read-only chat.")
        finally:
            await self.writer.stop_thinking()
            self.writer.end_answer()
            self.busy = False
            self.turn_id = None

    async def interrupt(self):
        self._stop_requested = True
        if self.busy and self.turn_id and not self._interrupt_sent:
            self._interrupt_sent = True
            try:
                await self.client.interrupt(self.thread_id, self.turn_id)
            except BaseException:
                self._interrupt_sent = False
                raise

    async def command(self, text):
        command, _, value = text.partition(" ")
        if command in {"/exit", "/quit"}:
            return False
        if command == "/new":
            self.thread_id = None
            self.last_answer = ""
            self._remember()
        elif command == "/sessions":
            self._sessions = await self.client.list_threads(limit=50)
            for number, thread in enumerate(self._sessions, 1):
                title = thread.get("name") or thread.get("title") or thread.get("preview") or thread["id"]
                self.writer.message("system", f"{number}. {' '.join(str(title).split())[:100]}")
            if not self._sessions:
                self.writer.notice("No saved sessions.")
        elif command == "/resume":
            value = value.strip()
            if value.isdigit() and 0 < int(value) <= len(self._sessions):
                value = self._sessions[int(value) - 1]["id"]
            if value:
                await self.resume(value)
            else:
                self.writer.notice("/resume SESSION_ID · or /sessions then /resume 1")
        elif command == "/help":
            self.writer.notice("Enter: send · Ctrl+J: newline · Ctrl+C: stop/exit · Ctrl+D: exit\n/new · /sessions · /resume ID · /exit")
        elif command == "/reconnect":
            await self.client.close()
            self.client = self.client_factory()
            await self.start()
        else:
            await self.ask(text)
        return True

    async def run(self):
        try:
            await self.start()
            if self.prompt is None:
                self.prompt = create_prompt()
            loop = asyncio.get_running_loop()
            previous_signal = signal.getsignal(signal.SIGINT)
            interrupt_tasks = set()

            def interrupt_done(task):
                interrupt_tasks.discard(task)
                if not task.cancelled() and (error := task.exception()):
                    self.writer.notice(str(error))

            def stop_turn():
                if self.busy:
                    task = asyncio.create_task(self.interrupt())
                    interrupt_tasks.add(task)
                    task.add_done_callback(interrupt_done)
                elif self.prompt.app.is_running:
                    self.prompt.app.exit(exception=KeyboardInterrupt())

            loop.add_signal_handler(signal.SIGINT, stop_turn)
            try:
                while True:
                    try:
                        text = await self.prompt.prompt_async(handle_sigint=False)
                    except (EOFError, KeyboardInterrupt):
                        break
                    if not text.strip():
                        continue
                    try:
                        if text.startswith("/"):
                            if not await self.command(text):
                                break
                        else:
                            await self.ask(text)
                    except Exception as exc:
                        self.writer.notice(str(exc))
            finally:
                loop.remove_signal_handler(signal.SIGINT)
                signal.signal(signal.SIGINT, previous_signal)
                for task in interrupt_tasks:
                    task.cancel()
                await asyncio.gather(*interrupt_tasks, return_exceptions=True)
        finally:
            await self.close()

    async def close(self):
        if self.closed:
            return
        self.closed = True
        await self.writer.stop_thinking()
        self.writer.end_answer()
        await self.client.close()
