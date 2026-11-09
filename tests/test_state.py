"""Independent terminal sessions must not overwrite each other's saved state."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

import pytest


pytestmark = pytest.mark.skipif(os.name == "nt", reason="The native chat uses POSIX file locking")

if os.name != "nt":
    from codex95.state import State


def test_existing_instances_merge_sessions_and_forgetting_preserves_other_projects(tmp_path):
    first = State(tmp_path)
    second = State(tmp_path)

    first.remember("/project-a", "thread-a")
    second.remember("/project-b", "thread-b")
    saved = State(tmp_path)
    assert saved.data == {"sessions": {"/project-a": "thread-a", "/project-b": "thread-b"}}
    assert saved.last_session("/project-a") == "thread-a"
    assert saved.last_session("/project-b") == "thread-b"

    # first's cache predates project-b, including when removing its own session.
    first.remember("/project-a", None)
    assert State(tmp_path).data == {"sessions": {"/project-b": "thread-b"}}
    assert first.last_session("/project-a") is None
    assert first.last_session("/project-b") == "thread-b"


def test_updates_preserve_existing_file_format_and_unknown_preferences(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"sessions": {"/old": "existing"}, "preference": "retained"}))
    path.chmod(0o644)

    state = State(tmp_path)
    state.remember("/новый", "thread-new")
    assert json.loads(path.read_text()) == {
        "sessions": {"/old": "existing", "/новый": "thread-new"}, "preference": "retained",
    }
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".state-*.tmp"))


def test_failed_replace_keeps_previous_state_and_cleans_temporary_file(tmp_path, monkeypatch):
    state = State(tmp_path)
    state.remember("/existing", "thread-old")

    def fail_replace(source, target):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replacement failure"):
        state.remember("/new", "thread-new")

    assert State(tmp_path).data == {"sessions": {"/existing": "thread-old"}}
    assert state.data == {"sessions": {"/existing": "thread-old"}}
    assert not list(tmp_path.glob(".state-*.tmp"))


def test_concurrent_processes_do_not_lose_projects_or_expose_partial_json(tmp_path):
    # Each writer constructs State before the barrier, reproducing stale caches.
    script = """
import sys
from pathlib import Path
from codex95.state import State

directory = Path(sys.argv[1])
writer = sys.argv[2]
state = State(directory)
print('ready', flush=True)
sys.stdin.readline()
for revision in range(30):
    state.remember('/project-' + writer, 'thread-' + writer + '-' + str(revision))
"""
    writers = [subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path), str(index)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    ) for index in range(4)]
    path = tmp_path / "state.json"
    try:
        for writer in writers:
            assert writer.stdout.readline().strip() == "ready"
        for writer in writers:
            writer.stdin.write("go\n")
            writer.stdin.flush()

        deadline = time.monotonic() + 20
        while any(writer.poll() is None for writer in writers):
            assert time.monotonic() < deadline, "Concurrent state writers did not finish"
            try:
                value = json.loads(path.read_text())
            except FileNotFoundError:
                continue
            assert isinstance(value["sessions"], dict)
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            time.sleep(0.001)

        for writer in writers:
            _, error = writer.communicate(timeout=5)
            assert writer.returncode == 0, error
    finally:
        for writer in writers:
            if writer.poll() is None:
                writer.kill()
            writer.communicate(timeout=5)

    assert State(tmp_path).data == {"sessions": {
        f"/project-{index}": f"thread-{index}-29" for index in range(4)
    }}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".state-*.tmp"))
