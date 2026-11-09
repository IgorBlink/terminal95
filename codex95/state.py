"""Small UI preferences file; credentials remain with the official CLI."""

import fcntl
import json
import os
from pathlib import Path
import tempfile

from platformdirs import user_state_path


class State:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or Path(
            os.environ.get("CODEX95_STATE_DIR", str(user_state_path("Codex95", appauthor=False)))
        )
        self.path = self.directory / "state.json"
        self.data = self._read()

    def _read(self) -> dict:
        try:
            value = json.loads(self.path.read_text())
            data = value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            data = {}
        if not isinstance(data.get("sessions"), dict):
            data["sessions"] = {}
        return data

    def last_session(self, cwd: str) -> str | None:
        value = self.data["sessions"].get(cwd)
        return value if isinstance(value, str) else None

    def remember(self, cwd: str, thread_id: str | None) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Lock a stable file: state.json itself is replaced during each update.
        lock_fd = os.open(self.directory / "state.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(lock_fd, "a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            # Another terminal may have changed a different project's session
            # since this State object was constructed.
            data = self._read()
            if thread_id:
                data["sessions"][cwd] = thread_id
            else:
                data["sessions"].pop(cwd, None)
            self._write(data)
            self.data = data

    def _write(self, data: dict) -> None:
        fd, name = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=self.directory)
        temporary = Path(name)
        try:
            # mkstemp creates the file with mode 0600 before any data is written.
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
