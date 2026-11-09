"""Process-boundary checks; these do not require network or Codex credentials."""

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock

from codex95.backend import CodexClient, ProtocolError


class ResumeHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_quiet_resume_keeps_saved_thread_without_downloading_transcript(self):
        client = CodexClient()
        client._rpc = AsyncMock(return_value={
            "thread": {"id": "saved-thread", "cwd": "/saved/project"},
            "model": "configured-model",
        })

        thread = await client.resume_thread("saved-thread", include_history=False)

        self.assertEqual(thread["id"], "saved-thread")
        self.assertEqual(thread["cwd"], "/saved/project")
        self.assertEqual(thread["turns"], [])
        self.assertEqual(client.model, "configured-model")
        client._rpc.assert_awaited_once()
        method, params = client._rpc.call_args.args
        self.assertEqual(method, "thread/resume")
        self.assertEqual(params["threadId"], "saved-thread")
        self.assertTrue(params["excludeTurns"])

    async def test_normal_resume_loads_all_pages_in_chronological_order(self):
        client = CodexClient()
        client._rpc = AsyncMock(side_effect=[
            {"thread": {"id": "saved-thread"}},
            {"data": [{"id": "third"}, {"id": "second"}], "nextCursor": "older"},
            {"data": [{"id": "first"}], "nextCursor": None},
        ])

        thread = await client.resume_thread("saved-thread")

        self.assertEqual([turn["id"] for turn in thread["turns"]], ["first", "second", "third"])
        calls = client._rpc.call_args_list
        self.assertEqual([call.args[0] for call in calls], ["thread/resume", "thread/turns/list", "thread/turns/list"])
        self.assertNotIn("cursor", calls[1].args[1])
        self.assertEqual(calls[2].args[1]["cursor"], "older")


class BackendProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup)
        self.clients = []

    async def _cleanup(self):
        for client in self.clients:
            await client.close()
        self.folder.cleanup()

    def make_client(self, behavior, timeout=5):
        path = Path(self.folder.name) / f"fake-codex-{len(self.clients)}"
        path.write_text(
            f"#!{sys.executable}\n"
            "import json,sys,time,os,subprocess\n"
            "def send(obj):\n print(json.dumps(obj),flush=True)\n"
            "for line in sys.stdin:\n"
            " m=json.loads(line)\n"
            " if m.get('method')=='initialize':\n"
            "  send({'id':m['id'],'result':{}})\n"
            + behavior,
            encoding="utf-8",
        )
        path.chmod(0o755)
        client = CodexClient(cwd=self.folder.name, executable=str(path), rpc_timeout=timeout)
        self.clients.append(client)
        return client

    async def test_unexpected_exit_fails_pending_call_and_notifies_ui(self):
        client = self.make_client(" elif m.get('method')=='thread/list':\n  sys.exit(7)\n")
        await client.start()
        with self.assertRaisesRegex(ProtocolError, "disconnected"):
            await client.list_threads()
        event = await asyncio.wait_for(client.events.get(), 1)
        self.assertEqual(event["method"], "codex95/disconnected")
        await client.close()
        self.assertEqual(client.process.returncode, 7)

    async def test_request_timeout_and_close_leave_no_pending_future(self):
        client = self.make_client(" elif m.get('method')=='thread/list':\n  pass\n")
        await client.start()
        client.rpc_timeout = 0.5  # Test the RPC timeout after process startup.
        with self.assertRaisesRegex(ProtocolError, "did not answer thread/list"):
            await client.list_threads()
        self.assertFalse(client._pending)
        await client.close()
        self.assertIsNotNone(client.process.returncode)
        await client.close()  # Closing twice is harmless.

    async def test_server_permission_request_is_declined_without_hanging(self):
        client = self.make_client(
            " elif m.get('method')=='thread/list':\n"
            "  req=m['id']\n"
            "  send({'id':'approval','method':'item/commandExecution/requestApproval','params':{'threadId':'test'}})\n"
            " elif m.get('id')=='approval':\n"
            "  send({'id':req,'result':{'data':[{'decision':m['result']['decision']}]}})\n"
        )
        await client.start()
        result = await client.list_threads()
        self.assertEqual(result, [{"decision": "decline"}])
        event = await asyncio.wait_for(client.events.get(), 1)
        self.assertEqual(event["method"], "codex95/requestDeclined")

    @unittest.skipIf(sys.platform == "win32", "POSIX private process group")
    async def test_close_terminates_own_descendant_process(self):
        client = self.make_client(
            " elif m.get('method')=='thread/list':\n"
            "  child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(90)'])\n"
            "  send({'id':m['id'],'result':{'data':[{'pid':child.pid}]}})\n"
        )
        await client.start()
        child_pid = (await client.list_threads())[0]["pid"]
        os.kill(child_pid, 0)
        await client.close()
        # The server exits on stdin EOF; its child is then killed by the
        # client-owned process-group cleanup, including after parent exit.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.05)
        else:
            self.fail(f"Codex child {child_pid} survived close()")


if __name__ == "__main__":
    unittest.main()
