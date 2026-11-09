"""Native terminal behavior; fake Codex transport and actual prompt input."""

import asyncio
import io
import signal
from pathlib import Path

import pytest
from prompt_toolkit.formatted_text import fragment_list_to_text, to_formatted_text
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from codex95.state import State
from codex95.terminal import NativeWriter, TerminalChat, create_prompt, safe_text


class FakeWriter:
    def __init__(self):
        self.writes = []
        self.messages = []
        self.notices = []
        self.begun = 0
        self.ended = 0

    def begin_answer(self):
        self.begun += 1

    async def start_thinking(self):
        pass

    async def stop_thinking(self):
        pass

    def write(self, text):
        self.writes.append(text)

    def end_answer(self):
        self.ended += 1

    def message(self, role, text):
        self.messages.append((role, text))

    def notice(self, text):
        self.notices.append(text)


class FakeClient:
    model = "test-model"

    def __init__(self):
        self.events = asyncio.Queue()
        self.created = []
        self.sent = []
        self.resumed = []
        self.resume_history_options = []
        self.interrupts = []
        self.close_count = 0
        self.answers = []
        self.new_gate = None

    async def start(self):
        pass

    async def close(self):
        self.close_count += 1

    async def new_thread(self, cwd):
        if self.new_gate is not None:
            await self.new_gate.wait()
        identifier = f"thread-{len(self.created)}"
        self.created.append((identifier, cwd))
        return {"id": identifier, "cwd": cwd, "turns": []}

    async def resume_thread(self, thread_id, cwd=None, *, include_history=True):
        self.resumed.append(thread_id)
        self.resume_history_options.append(include_history)
        return {"id": thread_id, "cwd": cwd, "turns": [
            {"items": [
                {"type": "userMessage", "content": [{"type": "text", "text": "old question"}]},
                {"type": "agentMessage", "text": "old answer"},
            ]},
        ]}

    async def send_turn(self, thread_id, text):
        turn_id = f"turn-{len(self.sent)}"
        self.sent.append((thread_id, text, turn_id))
        if self.answers:
            for method, values in self.answers.pop(0):
                params = {"threadId": thread_id, "turnId": turn_id, **values}
                await self.events.put({"method": method, "params": params})
        return {"id": turn_id, "status": "inProgress"}

    async def interrupt(self, thread_id, turn_id):
        self.interrupts.append((thread_id, turn_id))
        await self.events.put({"method": "turn/completed", "params": {
            "threadId": thread_id, "turnId": turn_id,
            "turn": {"id": turn_id, "status": "interrupted"},
        }})


def completed(text, *, delta=True):
    values = []
    if delta:
        values.extend([
            ("item/agentMessage/delta", {"itemId": "answer", "delta": text[:2]}),
            ("item/agentMessage/delta", {"itemId": "answer", "delta": text[2:]}),
        ])
    values.extend([
        ("item/completed", {"item": {"type": "agentMessage", "id": "answer", "text": text}}),
        ("turn/completed", {"turn": {"status": "completed"}}),
    ])
    return values


def make_chat(tmp_path: Path, client=None, **kwargs):
    client = client or FakeClient()
    writer = FakeWriter()
    state = State(tmp_path / "state")
    chat = TerminalChat(str(tmp_path), client=client, writer=writer, state=state, **kwargs)
    return chat, client, writer, state


async def test_stream_deduplicates_final_item_and_followup_keeps_session(tmp_path):
    chat, client, writer, state = make_chat(tmp_path)
    client.answers = [completed("Hello world"), completed("I remember")]
    await chat.start()
    try:
        assert await chat.ask("hi") == "Hello world"
        assert "".join(writer.writes) == "Hello world"
        assert writer.begun == writer.ended == 1
        assert state.last_session(str(tmp_path)) == chat.thread_id
        assert await chat.ask("remember?") == "I remember"
        assert chat.last_answer == "I remember"
        assert len(client.created) == 1
        assert client.sent[0][0] == client.sent[1][0]
    finally:
        await chat.close()


async def test_completion_only_answer_is_rendered(tmp_path):
    chat, client, writer, _ = make_chat(tmp_path)
    client.answers = [completed("No delta events", delta=False)]
    await chat.start()
    try:
        assert await chat.ask("hello") == "No delta events"
        assert "".join(writer.writes) == "No delta events"
    finally:
        await chat.close()


async def test_other_thread_and_turn_events_do_not_pollute_answer(tmp_path):
    chat, client, writer, _ = make_chat(tmp_path)
    client.answers = [[
        ("item/agentMessage/delta", {"threadId": "another-thread", "itemId": "a", "delta": "WRONG THREAD"}),
        ("item/agentMessage/delta", {"turnId": "another-turn", "itemId": "a", "delta": "WRONG TURN"}),
        *completed("Correct"),
    ]]
    await chat.start()
    try:
        assert await chat.ask("question") == "Correct"
        assert "".join(writer.writes) == "Correct"
    finally:
        await chat.close()


async def test_restart_resumes_context_without_reprinting_saved_transcript(tmp_path):
    state = State(tmp_path / "state")
    state.remember(str(tmp_path), "saved-thread")
    client, writer = FakeClient(), FakeWriter()
    chat = TerminalChat(str(tmp_path), client=client, writer=writer, state=state)
    client.answers = [completed("continued")]
    await chat.start()
    try:
        assert client.resumed == ["saved-thread"]
        assert client.resume_history_options == [False]
        assert writer.messages == writer.writes == writer.notices == []
        assert await chat.ask("followup") == "continued"
        assert client.sent[0][0] == "saved-thread"
        assert client.created == []
        assert "".join(writer.writes) == "continued"
    finally:
        await chat.close()


async def test_manual_resume_still_shows_history_when_requested(tmp_path):
    chat, client, writer, _ = make_chat(tmp_path)
    await chat.start()
    try:
        await chat.command("/resume saved-thread")
        assert client.resume_history_options == [True]
        assert writer.messages == [("user", "old question"), ("assistant", "old answer")]
        assert chat.last_answer == "old answer"
    finally:
        await chat.close()


async def test_reconnect_does_not_replay_old_messages(tmp_path):
    chat, client, writer, state = make_chat(tmp_path)
    fresh = FakeClient()
    chat.client_factory = lambda: fresh
    chat.thread_id = "saved-thread"
    state.remember(str(tmp_path), chat.thread_id)
    await chat.start()
    try:
        await chat.command("/reconnect")
        assert client.close_count == 1
        assert fresh.resumed == ["saved-thread"]
        assert fresh.resume_history_options == [False]
        assert writer.messages == writer.writes == writer.notices == []
        assert chat.thread_id == "saved-thread"
    finally:
        await chat.close()


async def test_interrupt_stops_active_turn_and_returns_partial_answer(tmp_path):
    chat, client, writer, _ = make_chat(tmp_path)
    client.answers = [[("item/agentMessage/delta", {"itemId": "answer", "delta": "partial"})]]
    await chat.start()
    task = asyncio.create_task(chat.ask("question"))
    try:
        async with asyncio.timeout(1):
            while chat.turn_id is None:
                await asyncio.sleep(0)
        await chat.interrupt()
        assert await asyncio.wait_for(task, 1) == "partial"
        assert client.interrupts == [(chat.thread_id, "turn-0")]
        assert writer.begun == writer.ended == 1
    finally:
        if not task.done():
            task.cancel()
        await chat.close()


async def test_interrupt_before_thread_and_turn_exist_is_not_lost(tmp_path):
    chat, client, _, _ = make_chat(tmp_path)
    client.new_gate = asyncio.Event()
    await chat.start()
    task = asyncio.create_task(chat.ask("question"))
    try:
        await asyncio.sleep(0)
        await chat.interrupt()
        client.new_gate.set()
        await asyncio.wait_for(task, 1)
        assert client.interrupts == [(chat.thread_id, "turn-0")]
    finally:
        if not task.done():
            task.cancel()
        await chat.close()


async def test_disconnect_during_answer_does_not_hang_and_close_is_idempotent(tmp_path):
    chat, client, _, _ = make_chat(tmp_path)
    client.answers = [[("codex95/disconnected", {"message": "process died"})]]
    await chat.start()
    try:
        with pytest.raises(RuntimeError, match="process died"):
            await asyncio.wait_for(chat.ask("question"), 1)
    finally:
        await chat.close()
        await chat.close()
    assert client.close_count == 1


async def test_native_prompt_is_only_arrow_without_full_screen_and_paste_waits_for_enter():
    with create_pipe_input() as input_pipe:
        prompt = create_prompt(input=input_pipe, output=DummyOutput())
        assert fragment_list_to_text(to_formatted_text(prompt.message)) == "> "
        assert prompt.app.full_screen is False
        assert prompt.bottom_toolbar is None
        assert prompt.rprompt is None
        task = asyncio.create_task(prompt.prompt_async())
        try:
            await asyncio.sleep(0.02)
            input_pipe.send_text("\x1b[200~first\nsecond\x1b[201~")
            await asyncio.sleep(0.02)
            assert not task.done()
            input_pipe.send_text("\r")
            assert await asyncio.wait_for(task, 1) == "first\nsecond"
        finally:
            if not task.done():
                task.cancel()


async def test_ctrl_j_adds_newline_and_ctrl_d_exits_empty_prompt():
    with create_pipe_input() as input_pipe:
        prompt = create_prompt(input=input_pipe, output=DummyOutput())
        task = asyncio.create_task(prompt.prompt_async())
        await asyncio.sleep(0.02)
        input_pipe.send_text("first\x0asecond\r")
        assert await asyncio.wait_for(task, 1) == "first\nsecond"
        task = asyncio.create_task(prompt.prompt_async())
        await asyncio.sleep(0.02)
        input_pipe.send_text("\x04")
        with pytest.raises(EOFError):
            await asyncio.wait_for(task, 1)


def test_writer_is_only_arrows_and_text_without_chrome():
    stream = io.StringIO()
    writer = NativeWriter(stream)
    assert stream.getvalue() == ""
    writer.message("user", "hello")
    writer.begin_answer()
    writer.write("first\nsecond")
    writer.end_answer()
    assert stream.getvalue() == "> hello\n\n> first\n  second\n\n"


def test_model_text_cannot_emit_terminal_controls():
    text = "Привет\nhello\tworld\x1b[2J\x00\b\x9b"
    assert safe_text(text) == "Привет\nhello\tworld[2J"


async def test_failed_interrupt_can_be_retried(tmp_path):
    class FlakyClient(FakeClient):
        attempts = 0

        async def interrupt(self, thread_id, turn_id):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("temporary interrupt error")
            await super().interrupt(thread_id, turn_id)

    chat, client, _, _ = make_chat(tmp_path, FlakyClient())
    await chat.start()
    task = asyncio.create_task(chat.ask("question"))
    try:
        async with asyncio.timeout(1):
            while chat.turn_id is None:
                await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="temporary interrupt error"):
            await chat.interrupt()
        await chat.interrupt()
        await asyncio.wait_for(task, 1)
        assert client.attempts == 2
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await chat.close()


async def test_run_has_no_startup_banner_and_closes_on_eof(tmp_path):
    class FakePrompt:
        calls = 0

        async def prompt_async(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return "hello"
            raise EOFError

    chat, client, writer, _ = make_chat(tmp_path, prompt=FakePrompt())
    client.answers = [completed("reply")]
    original_handler = signal.getsignal(signal.SIGINT)
    await chat.run()
    assert writer.notices == []
    assert writer.messages == []
    assert "".join(writer.writes) == "reply"
    assert client.close_count == 1
    assert signal.getsignal(signal.SIGINT) == original_handler
