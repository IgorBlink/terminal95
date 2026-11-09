import argparse
import asyncio
import sys
from pathlib import Path

from . import __version__


def main():
    parser = argparse.ArgumentParser(description="Codex 95 — терминальный чат в стиле старой Windows")
    parser.add_argument("--cwd", default=str(Path.cwd()), help="Рабочая папка Codex")
    parser.add_argument("--session", help="Открыть сохранённую сессию по ID")
    parser.add_argument("--new", action="store_true", help="Начать новый разговор")
    parser.add_argument("--demo", action="store_true", help="Демонстрация без подключения к Codex")
    parser.add_argument("--inline", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--doctor", action="store_true", help="Проверить подключение к Codex")
    parser.add_argument("--version", action="version", version=f"Codex95 {__version__}")
    args = parser.parse_args()
    if not Path(args.cwd).is_dir():
        parser.error(f"Папка не существует: {args.cwd}")
    if args.doctor:
        asyncio.run(doctor())
        return
    from .terminal import TerminalChat
    from .state import State
    state = State()
    if args.new and not args.demo:
        state.remember(str(Path(args.cwd).resolve()), None)
    app = TerminalChat(args.cwd, session=args.session, demo=args.demo, state=state)
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"> {exc}", file=sys.stderr)
        raise SystemExit(1)


async def doctor():
    from .backend import CodexClient
    client = CodexClient()
    try:
        await client.start()
        threads = await client.list_threads(limit=1)
        print("OK: Codex app-server отвечает. Подключение к локальному CLI работает.")
        print(f"Сохранённые сессии: {'доступны' if threads else 'пока нет'}.")
        print("Для проверки входа: codex login status")
    except Exception as exc:
        print(f"Ошибка подключения: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        await client.close()


if __name__ == "__main__":
    main()
