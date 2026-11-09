"""Markdown presentation through the real native writer and chat event loop."""

import asyncio
import io
import re

import pytest

from codex95.state import State
from codex95.terminal import NativeWriter, TerminalChat


class TTYBuffer(io.StringIO):
    def isatty(self):
        return True


def visible(text):
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)


async def until(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


class EventClient:
    """A local transport double; tests exercise the real presentation layer."""

    def __init__(self):
        self.events = asyncio.Queue()
        self.turn_count = 0
        self.thread_id = "thread"
        self.turn_id = None
        self.history_options = []
        self.history = []

    async def start(self):
        pass

    async def close(self):
        pass

    async def new_thread(self, cwd):
        return {"id": self.thread_id, "cwd": cwd}

    async def resume_thread(self, thread_id, *, include_history=True):
        self.history_options.append(include_history)
        return {"id": thread_id, "turns": self.history if include_history else []}

    async def send_turn(self, thread_id, text):
        self.turn_count += 1
        self.turn_id = f"turn-{self.turn_count}"
        return {"id": self.turn_id}

    async def emit(self, method, **params):
        await self.events.put({"method": method, "params": {
            "threadId": self.thread_id, "turnId": self.turn_id, **params,
        }})

    async def interrupt(self, thread_id, turn_id):
        await self.emit("turn/completed", turn={"id": turn_id, "status": "interrupted"})


def make_chat(tmp_path, *, session=None):
    stream = TTYBuffer()
    writer = NativeWriter(stream)
    client = EventClient()
    chat = TerminalChat(str(tmp_path), client=client, writer=writer,
                        state=State(tmp_path / "state"), session=session)
    return chat, client, writer, stream


def test_assistant_markdown_handles_markers_split_between_deltas(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    stream = TTYBuffer()
    writer = NativeWriter(stream)
    writer.begin_answer()
    for character in "# Header\n\n**bold** and `code`.\n\n":
        writer.write(character)
    writer.end_answer()

    output = visible(stream.getvalue())
    assert output.count("Header") == 1
    assert "bold and code." in output
    assert "# Header" not in output
    assert "**" not in output
    assert "`" not in output
    assert "\x1b[?1049" not in stream.getvalue()


def test_code_fence_content_is_literal_even_when_fences_are_split(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    stream = TTYBuffer()
    writer = NativeWriter(stream)
    for chunk in ("`", "``tex", "t\n# comment **not-bold** foo_bar\n`", "`", "`\n"):
        writer.write(chunk)
    writer.end_answer()

    output = stream.getvalue()
    assert "# comment **not-bold** foo_bar" in output
    assert "```" not in output
    assert "\x1b" not in output


@pytest.mark.parametrize("role", ["user", "system"])
def test_user_and_system_text_never_becomes_markdown(monkeypatch, role):
    monkeypatch.setenv("NO_COLOR", "1")
    stream = TTYBuffer()
    writer = NativeWriter(stream)
    writer.message(role, "# literal\n**keep stars** and `backticks` [label](file:///tmp/example)")

    assert stream.getvalue() == (
        "> # literal\n  **keep stars** and `backticks` [label](file:///tmp/example)\n\n"
    )


def test_no_color_tty_renders_markdown_without_accepting_terminal_controls(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    stream = TTYBuffer()
    writer = NativeWriter(stream)
    writer.message("assistant", "**Readable** `text`\n\n\x1b]52;c;payload\x07\x9b\x00")

    output = stream.getvalue()
    assert "Readable text" in output
    assert "**" not in output
    assert "`" not in output
    assert not any(control in output for control in ("\x1b", "\x07", "\x9b", "\x00"))


def test_redirected_output_preserves_raw_markdown_without_ansi():
    stream = io.StringIO()
    writer = NativeWriter(stream)
    writer.message("assistant", "# Heading\n**bold** and `code`\n```text\n*literal*\n```")

    assert stream.getvalue() == (
        "> # Heading\n  **bold** and `code`\n  ```text\n  *literal*\n  ```\n\n"
    )
    assert "\x1b" not in stream.getvalue()


async def test_indicator_remains_until_a_complete_markdown_block_is_visible(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    stream = TTYBuffer()
    writer = NativeWriter(stream)
    writer.begin_answer()
    await writer.start_thinking()
    try:
        writer.write("**Still")
        assert writer._thinking_visible
        assert "Still" not in stream.getvalue()

        writer.write(" thinking.**\n\n")
        assert "Still thinking." in visible(stream.getvalue())
        assert not writer._thinking_visible
        rendered = stream.getvalue()
        await asyncio.sleep(0)
        assert stream.getvalue() == rendered
    finally:
        await writer.stop_thinking()
        writer.end_answer()


@pytest.mark.parametrize("with_deltas", [False, True])
async def test_chat_formats_reply_once_for_final_only_and_streamed_completion(tmp_path, monkeypatch, with_deltas):
    monkeypatch.setenv("NO_COLOR", "1")
    chat, client, writer, stream = make_chat(tmp_path)
    await chat.start()
    task = asyncio.create_task(chat.ask("hello"))
    raw = "**One reply.**"
    try:
        await until(lambda: chat.turn_id == "turn-1")
        if with_deltas:
            for delta in ("*", "*One reply.", "**"):
                await client.emit("item/agentMessage/delta", itemId="answer", delta=delta)
        await client.emit("item/completed", item={"id": "answer", "type": "agentMessage", "text": raw})
        await client.emit("turn/completed", turn={"id": "turn-1", "status": "completed"})

        assert await asyncio.wait_for(task, 1) == raw
        assert chat.last_answer == raw
        output = visible(stream.getvalue())
        assert output.count("One reply.") == 1
        assert "**" not in output
        assert not writer._thinking_visible
        assert not writer.open
        assert writer._thinking_task is None or writer._thinking_task.done()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await chat.close()


async def test_interrupt_flushes_unfinished_code_and_next_reply_resets_markdown(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    chat, client, writer, stream = make_chat(tmp_path)
    await chat.start()
    task = asyncio.create_task(chat.ask("show code"))
    raw = "```text\n# literal **inside**"
    followup = None
    try:
        await until(lambda: chat.turn_id == "turn-1")
        await client.emit("item/agentMessage/delta", itemId="code", delta=raw)
        await until(lambda: chat.last_answer == raw)
        await chat.interrupt()
        assert await asyncio.wait_for(task, 1) == raw
        assert "# literal **inside**" in visible(stream.getvalue())
        assert not writer._thinking_visible

        followup = asyncio.create_task(chat.ask("continue"))
        await until(lambda: chat.turn_id == "turn-2")
        await client.emit("item/completed", item={"id": "next", "type": "agentMessage", "text": "**Fresh reply.**"})
        await client.emit("turn/completed", turn={"id": "turn-2", "status": "completed"})
        assert await asyncio.wait_for(followup, 1) == "**Fresh reply.**"
        output = visible(stream.getvalue())
        assert output.count("# literal **inside**") == 1
        assert output.count("Fresh reply.") == 1
        assert "**Fresh reply.**" not in output
        assert "```" not in output
    finally:
        for pending in (task, followup):
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        await chat.close()


async def test_quiet_resume_stays_empty_and_explicit_history_formats_only_assistant(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    chat, client, _, stream = make_chat(tmp_path, session="thread")
    client.history = [{"items": [
        {"type": "userMessage", "content": [{"type": "text", "text": "**user-literal**"}]},
        {"type": "agentMessage", "text": "**assistant-rendered**"},
    ]}]
    try:
        await chat.start()
        assert stream.getvalue() == ""
        await chat.resume("thread", show_history=True)

        output = stream.getvalue()
        assert "**user-literal**" in output
        assert "assistant-rendered" in output
        assert "**assistant-rendered**" not in output
        assert client.history_options == [False, True]
    finally:
        await chat.close()
