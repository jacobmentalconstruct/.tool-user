"""Start one of Local Memory Lab's interfaces."""

from __future__ import annotations

import sys
from pathlib import Path


sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def main(argv: list[str] | None = None):
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in {"hub-open", "hub-server", "client"}:
        raise SystemExit("Usage: python lab.py {hub-open|hub-server|client} [options]")
    command, *rest = args
    if command == "hub-open":
        from local_memory_lab.interfaces.launcher import main as launch
        launch()
    elif command == "hub-server":
        from local_memory_lab.interfaces.web import main as serve
        serve()
    else:
        from local_memory_lab.interfaces.client import main as client
        try:
            client(rest)
        except (RuntimeError, KeyboardInterrupt) as exc:
            if str(exc):
                print(str(exc), file=sys.stderr)
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()
