from __future__ import annotations

from multiprocessing import freeze_support

from agent_farm.desktop_server import main


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
