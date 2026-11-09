#!/bin/sh
set -eu
app_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -x "$app_dir/.venv/bin/python" ]; then
    export PYTHONPATH="$app_dir${PYTHONPATH:+:$PYTHONPATH}"
    exec "$app_dir/.venv/bin/python" -m codex95 "$@"
fi
if command -v uv >/dev/null 2>&1; then
    exec uv run --project "$app_dir" --locked --no-dev codex95 "$@"
elif [ -x "$HOME/.local/bin/uv" ]; then
    exec "$HOME/.local/bin/uv" run --project "$app_dir" --locked --no-dev codex95 "$@"
fi
echo 'Для запуска нужен uv: https://docs.astral.sh/uv/getting-started/installation/' >&2
exit 1
