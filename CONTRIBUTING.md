# Contributing

Small improvements, terminal profiles, and bug fixes are welcome. For a large change, open an issue first so the approach can be discussed.

## Local setup

Python 3.11+, uv, and macOS or Linux are needed for the optional chat and its tests. Theme-only changes do not require a Codex account. Native Windows chat is not supported; the Windows Terminal theme can still be used independently.

```sh
git clone https://github.com/IgorBlink/terminal95.git
cd terminal95
uv sync
uv run pytest tests
uv run codex95 --demo
```

The demo does not connect to Codex. For a real conversation, install and authenticate the official Codex CLI, then run `uv run codex95`.

## Changes to check

- Keep the terminal style usable independently of the optional chat.
- Keep profiles limited to appearance; do not change startup commands or security settings.
- Preserve terminal scrollback, the caller's working folder, and ordinary text selection.
- Add a regression test for a behavior fix. Keep tests independent of network access and real credentials.
- For a new profile, name the terminal and version you tested, verify its configuration format, and include a preview when possible.
- Keep font attribution and its separate license intact. The bundled font is not covered by the code's MIT license.

Open a pull request with a short explanation of the problem, the resulting behavior, and what you verified. Report the terminal, operating system, and Codex CLI version for compatibility issues.
