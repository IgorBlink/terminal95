"""Cross-format palette consistency and safe terminal-only color application."""

import errno
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[1]
THEMES = ROOT / "themes"
PALETTE = json.loads((THEMES / "palette.json").read_text())


def test_structured_profiles_match_the_shared_palette():
    alacritty = tomllib.loads((THEMES / "terminal95.alacritty.toml").read_text())
    assert alacritty["font"]["normal"]["family"] == PALETTE["font"]["family"]
    assert alacritty["colors"]["primary"] == {
        "background": PALETTE["background"], "foreground": PALETTE["foreground"],
    }
    names = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"]
    for section, offset in (("normal", 0), ("bright", 8)):
        assert [alacritty["colors"][section][name] for name in names] == PALETTE["ansi"][offset:offset + 8]

    windows = json.loads((THEMES / "terminal95.windows-terminal.json").read_text())
    scheme = windows["schemes"][0]
    assert scheme["background"] == PALETTE["background"]
    assert scheme["foreground"] == PALETTE["foreground"]
    assert windows["profiles"]["defaults"]["font"]["face"] == PALETTE["font"]["family"]
    windows_names = ["black", "red", "green", "yellow", "blue", "purple", "cyan", "white",
                     "brightBlack", "brightRed", "brightGreen", "brightYellow", "brightBlue", "brightPurple", "brightCyan", "brightWhite"]
    assert [scheme[name] for name in windows_names] == PALETTE["ansi"]

    iterm = plistlib.loads((THEMES / "Terminal95.itermcolors").read_bytes())
    for index, color in enumerate(PALETTE["ansi"]):
        values = iterm[f"Ansi {index} Color"]
        actual = "#" + "".join(f"{round(values[f'{channel} Component'] * 255):02x}" for channel in ("Red", "Green", "Blue"))
        assert actual == color


def test_text_profiles_have_consistent_palette_without_startup_commands():
    ghostty = (THEMES / "terminal95.ghostty").read_text()
    kitty = (THEMES / "terminal95.kitty.conf").read_text()
    for index, color in enumerate(PALETTE["ansi"]):
        assert f"palette = {index}={color}" in ghostty
        assert f"color{index} {color}" in kitty
    assert "background = cccccc" in ghostty
    assert "background #cccccc" in kitty
    for line in ghostty.splitlines():
        if line.strip() and not line.startswith("#"):
            assert line.split("=", 1)[0].strip() not in {
                "command", "initial-command", "env", "window-save-state", "keybind", "config-file",
            }


def terminal_output(*args):
    import pty

    master, slave = pty.openpty()
    try:
        process = subprocess.Popen([str(ROOT / "scripts/theme.sh"), *args], stdout=slave,
                                   stderr=subprocess.PIPE, env={**os.environ, "TERM": "xterm-256color"})
        os.close(slave)
        slave = None
        output = bytearray()
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            output.extend(chunk)
        _, error = process.communicate(timeout=5)
        assert process.returncode == 0, error.decode()
        return bytes(output)
    finally:
        os.close(master)
        if slave is not None:
            os.close(slave)


@pytest.mark.skipif(os.name == "nt", reason="The temporary theme helper uses a POSIX shell and PTY")
def test_generic_script_only_emits_dynamic_colors_to_a_terminal():
    output = terminal_output()
    assert b"\x1b]10;#000000\x07" in output
    assert b"\x1b]11;#cccccc\x07" in output
    assert b"\x1b]12;#000000\x07" in output
    for index, color in enumerate(PALETTE["ansi"]):
        assert f"\x1b]4;{index};{color}\x07".encode() in output
    assert re.sub(rb"\x1b\](?:4;\d+|10|11|12);#[0-9a-f]{6}\x07", b"", output) == b""


@pytest.mark.skipif(os.name == "nt", reason="The temporary theme helper uses a POSIX shell and PTY")
def test_generic_script_reset_and_redirect_protection():
    assert terminal_output("--reset") == b"\x1b]104\x07\x1b]110\x07\x1b]111\x07\x1b]112\x07"
    result = subprocess.run([str(ROOT / "scripts/theme.sh")], capture_output=True)
    assert result.returncode == 1
    assert result.stdout == b""
    assert b"run this directly" in result.stderr
    invalid = subprocess.run([str(ROOT / "scripts/theme.sh"), "--unexpected"], capture_output=True)
    assert invalid.returncode == 2
    assert b"\x1b" not in invalid.stdout + invalid.stderr
