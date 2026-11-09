"""The optional Codex chat stays in the caller's terminal and directory."""

from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def entrypoint(tmp_path, monkeypatch):
    from codex95 import __main__, state, terminal

    monkeypatch.delenv("CODEX95_STYLED_TERMINAL", raising=False)
    monkeypatch.chdir(tmp_path)
    calls = {"process": [], "native": [], "remember": [], "run": 0, "doctor": 0}

    def forbid_process(*args, **kwargs):
        calls["process"].append((args, kwargs))
        raise AssertionError("The native entrypoint must not invoke open or spawn a launcher")

    class FakeState:
        def remember(self, cwd, thread_id):
            calls["remember"].append((cwd, thread_id))

    class FakeChat:
        def __init__(self, cwd, **kwargs):
            calls["native"].append((cwd, kwargs))

        async def run(self):
            calls["run"] += 1

    async def fake_doctor():
        calls["doctor"] += 1

    monkeypatch.setattr(subprocess, "run", forbid_process)
    monkeypatch.setattr(terminal, "TerminalChat", FakeChat)
    monkeypatch.setattr(state, "State", FakeState)
    monkeypatch.setattr(__main__, "doctor", fake_doctor)
    return __main__, calls


def test_default_entrypoint_runs_in_callers_terminal_and_directory(entrypoint, tmp_path, monkeypatch):
    module, calls = entrypoint
    monkeypatch.setattr(module.sys, "argv", ["codex95", "--session", "session-95"])
    module.main()
    assert calls["process"] == []
    assert len(calls["native"]) == 1
    assert calls["native"][0][0] == str(tmp_path)
    assert calls["native"][0][1]["session"] == "session-95"
    assert calls["run"] == 1
    assert Path.cwd() == tmp_path


def test_explicit_working_directory_does_not_open_window_or_change_callers_cwd(entrypoint, tmp_path, monkeypatch):
    module, calls = entrypoint
    project = tmp_path / "project with spaces"
    project.mkdir()
    monkeypatch.setattr(module.sys, "argv", ["codex95", "--cwd", str(project)])
    module.main()
    assert calls["native"][0][0] == str(project)
    assert calls["run"] == 1
    assert calls["process"] == []
    assert Path.cwd() == tmp_path


@pytest.mark.parametrize("compatibility", ["inline-flag", "old-styled-env"])
def test_old_inline_flag_or_styled_environment_never_changes_native_default(entrypoint, tmp_path, monkeypatch, compatibility):
    module, calls = entrypoint
    args = ["--session", "session-95"]
    if compatibility == "old-styled-env":
        monkeypatch.setenv("CODEX95_STYLED_TERMINAL", "1")
    else:
        args.append("--inline")
    monkeypatch.setattr(module.sys, "argv", ["codex95", *args])
    module.main()
    assert calls["process"] == []
    assert len(calls["native"]) == 1
    assert calls["native"][0][0] == str(tmp_path)
    assert calls["native"][0][1]["session"] == "session-95"
    assert calls["run"] == 1


@pytest.mark.parametrize("demo", [False, True])
def test_new_resets_only_current_folder_session_outside_demo(entrypoint, tmp_path, monkeypatch, demo):
    module, calls = entrypoint
    args = ["--new"] + (["--demo"] if demo else [])
    monkeypatch.setattr(module.sys, "argv", ["codex95", *args])
    module.main()
    assert calls["remember"] == ([] if demo else [(str(tmp_path), None)])
    assert calls["native"][0][1]["demo"] == demo
    assert calls["run"] == 1
    assert calls["process"] == []


def test_invalid_directory_fails_before_chat_or_window_start(entrypoint, tmp_path, monkeypatch):
    module, calls = entrypoint
    monkeypatch.setattr(module.sys, "argv", ["codex95", "--cwd", str(tmp_path / "missing")])
    with pytest.raises(SystemExit) as result:
        module.main()
    assert result.value.code == 2
    assert calls["native"] == calls["process"] == []
    assert calls["run"] == 0


def test_doctor_stays_headless(entrypoint, monkeypatch):
    module, calls = entrypoint
    monkeypatch.setattr(module.sys, "argv", ["codex95", "--doctor"])
    module.main()
    assert calls["doctor"] == 1
    assert calls["process"] == calls["native"] == []


@pytest.mark.parametrize("flag", ["--version", "--help"])
def test_help_and_version_stay_headless(entrypoint, monkeypatch, capsys, flag):
    module, calls = entrypoint
    monkeypatch.setattr(module.sys, "argv", ["codex95", flag])
    with pytest.raises(SystemExit) as result:
        module.main()
    assert result.value.code == 0
    assert capsys.readouterr().out
    assert calls["process"] == calls["native"] == []
