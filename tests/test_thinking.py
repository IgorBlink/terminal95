"""The waiting indicator stays transient and never competes with reply text."""

import asyncio
import io

import pytest

from codex95.state import State
from codex95.terminal import NativeWriter, TerminalChat


class TTYBuffer(io.StringIO):
    def isatty(self):
        return True


class TrackingWriter:
    def __init__(self):
        self.thinking = False
        self.events = []
        self.text = []

    def begin_answer(self):
        self.events.append("begin")

    async def start_thinking(self):
        self.thinking = True
        self.events.append("thinking")

    async def stop_thinking(self):
        self.thinking = False
        self.events.append("stopped")

    def write(self, text):
        if text:
            assert not self.thinking, "Reply text must not race the indicator"
            self.events.append("reply")
            self.text.append(text)

    def end_answer(self):
        assert not self.thinking
        self.events.append("end")

    def message(self, role, text):
        self.events.append((role, text))

    def notice(self, text):
        assert not self.thinking
        self.events.append(("notice", text))


class WaitingClient:
    def __init__(self):
        self.events = asyncio.Queue()
        self.new_gate = None
        self.closed = 0
        self.sent = False

    async def start(self):
        pass

    async def new_thread(self, cwd):
        if self.new_gate is not None:
            await self.new_gate.wait()
        return {"id": "thread", "cwd": cwd}

    async def send_turn(self, thread_id, text):
        self.sent = True
        return {"id": "turn"}

    async def interrupt(self, thread_id, turn_id):
        await self.emit("turn/completed", turn={"id": "turn", "status": "interrupted"})

    async def close(self):
        self.closed += 1

    async def emit(self, method, **params):
        await self.events.put({"method": method, "params": {
            "threadId": "thread", "turnId": "turn", **params,
        }})


async def until(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


def chat_for(tmp_path, *, client=None, writer=None):
    client = client or WaitingClient()
    writer = writer or TrackingWriter()
    chat = TerminalChat(str(tmp_path), client=client, writer=writer,
                        state=State(tmp_path / "state"))
    return chat, client, writer


async def test_tty_indicator_animates_one_line_and_cannot_overwrite_reply(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(NativeWriter, "THINK_INTERVAL", 0.01)
    stream = TTYBuffer()
    writer = NativeWriter(stream)
    writer.begin_answer()
    await writer.start_thinking()
    try:
        await asyncio.sleep(0.045)
        waiting = stream.getvalue()
        assert "." in waiting and ".." in waiting and "..." in waiting
        assert "\n" not in waiting
        assert "\x1b[?1049" not in waiting  # No alternate-screen UI.
    finally:
        await writer.stop_thinking()
    task = writer._thinking_task
    assert task is None or task.done()
    writer.write("the reply")
    writer.end_answer()
    completed_output = stream.getvalue()
    await asyncio.sleep(0.03)
    assert stream.getvalue() == completed_output
    assert completed_output.rstrip().endswith("the reply")


async def test_redirected_output_has_no_indicator_or_terminal_controls(monkeypatch):
    monkeypatch.setattr(NativeWriter, "THINK_INTERVAL", 0.01)
    stream = io.StringIO()
    writer = NativeWriter(stream)
    writer.begin_answer()
    await writer.start_thinking()
    await asyncio.sleep(0.025)
    await writer.stop_thinking()
    writer.write("plain reply")
    writer.end_answer()
    assert stream.getvalue() == "> plain reply\n\n"


async def test_thinking_covers_thread_start_and_empty_deltas_until_reply(tmp_path):
    chat, client, writer = chat_for(tmp_path)
    client.new_gate = asyncio.Event()
    task = asyncio.create_task(chat.ask("question"))
    try:
        await until(lambda: writer.thinking)
        assert chat.turn_id is None
        client.new_gate.set()
        await until(lambda: chat.turn_id == "turn")
        await client.emit("item/agentMessage/delta", itemId="answer", delta="")
        await asyncio.sleep(0)
        assert writer.thinking
        await client.emit("item/agentMessage/delta", itemId="answer", delta="Hello")
        await until(lambda: bool(writer.text))
        assert not writer.thinking
        await client.emit("turn/completed", turn={"id": "turn", "status": "completed"})
        assert await task == "Hello"
        assert writer.events.index("stopped") < writer.events.index("reply")
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await chat.close()


async def test_completion_only_reply_removes_indicator_first(tmp_path):
    chat, client, writer = chat_for(tmp_path)
    task = asyncio.create_task(chat.ask("question"))
    try:
        await until(lambda: client.sent)
        await client.emit("item/completed", item={"type": "agentMessage", "id": "answer", "text": "Hello"})
        await client.emit("turn/completed", turn={"id": "turn", "status": "completed"})
        assert await task == "Hello"
        assert writer.text == ["Hello"]
        assert not writer.thinking
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await chat.close()


@pytest.mark.parametrize("ending", ["disconnect", "interrupt", "cancel"])
async def test_waiting_indicator_is_cleaned_up_for_non_reply_endings(tmp_path, ending):
    chat, client, writer = chat_for(tmp_path)
    task = asyncio.create_task(chat.ask("question"))
    try:
        await until(lambda: chat.turn_id == "turn")
        assert writer.thinking
        if ending == "disconnect":
            await client.emit("codex95/disconnected", message="connection lost")
            with pytest.raises(RuntimeError, match="connection lost"):
                await task
        elif ending == "interrupt":
            await chat.interrupt()
            assert await task == ""
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not writer.thinking
        assert writer.events[-1] == "end"
        assert not chat.busy
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await chat.close()


async def test_close_stops_indicator_and_joins_its_task(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(NativeWriter, "THINK_INTERVAL", 0.01)
    stream = TTYBuffer()
    writer = NativeWriter(stream)
    chat, client, _ = chat_for(tmp_path, writer=writer)
    writer.begin_answer()
    await writer.start_thinking()
    await chat.close()
    closed_output = stream.getvalue()
    await asyncio.sleep(0.03)
    assert stream.getvalue() == closed_output
    assert writer._thinking_task is None or writer._thinking_task.done()
    assert client.closed == 1
