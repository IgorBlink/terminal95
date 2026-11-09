"""Explicit offline demo used for UI previews and regression tests."""

import asyncio
import uuid


class DemoClient:
    model = "ДЕМО · без подключения"

    def __init__(self):
        self.events: asyncio.Queue[dict] = asyncio.Queue()
        self.tasks: set[asyncio.Task] = set()
        self.history: dict[str, list[dict]] = {}

    async def start(self):
        pass

    async def list_threads(self, limit=50):
        return []

    async def new_thread(self, cwd):
        identifier = str(uuid.uuid4())
        self.history[identifier] = []
        return {"id": identifier, "cwd": cwd, "model": self.model, "turns": []}

    async def resume_thread(self, thread_id, cwd=None, *, include_history=True):
        return {"id": thread_id, "cwd": cwd, "model": self.model,
                "turns": self.history.get(thread_id, []) if include_history else []}

    async def send_turn(self, thread_id, text):
        turn_id, item_id = str(uuid.uuid4()), str(uuid.uuid4())
        task = asyncio.create_task(self._answer(thread_id, turn_id, item_id, text))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return {"turn": {"id": turn_id, "status": "inProgress"}}

    async def _answer(self, thread_id, turn_id, item_id, text):
        answer = "Это демонстрационный ответ. В обычном режиме здесь отвечает Codex."
        base = {"threadId": thread_id, "turnId": turn_id}
        await self.events.put({"method": "turn/started", "params": {**base, "turn": {"id": turn_id}}})
        for word in answer.split(" "):
            await asyncio.sleep(0.018)
            await self.events.put({"method": "item/agentMessage/delta",
                                   "params": {**base, "itemId": item_id, "delta": word + " "}})
        item = {"type": "agentMessage", "id": item_id, "text": answer}
        self.history.setdefault(thread_id, []).append({"id": turn_id, "items": [
            {"type": "userMessage", "content": [{"type": "text", "text": text}]}, item]})
        await self.events.put({"method": "item/completed", "params": {**base, "item": item}})
        await self.events.put({"method": "turn/completed", "params": {**base, "turn": {"id": turn_id, "status": "completed"}}})

    async def interrupt(self, thread_id, turn_id):
        for task in list(self.tasks):
            task.cancel()
        await self.events.put({"method": "turn/completed", "params": {
            "threadId": thread_id, "turnId": turn_id,
            "turn": {"id": turn_id, "status": "interrupted"}}})

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
