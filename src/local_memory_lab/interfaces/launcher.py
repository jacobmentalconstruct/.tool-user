"""Open the current shared hub, or start one and open its browser page."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import webbrowser
from urllib.request import Request, urlopen

from ..locations import CONTROL, ROOT

CONFIG = CONTROL / "shared.json"


def running_session():
    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        request = Request(config["api_url"] + "/api/state",
                          headers={"Authorization": "Bearer " + config["agent_token"]})
        with urlopen(request, timeout=1) as response:
            if response.status == 200:
                return config
    except (OSError, ValueError, KeyError):
        pass
    return None


def main():
    config = running_session()
    if config is None:
        CONTROL.mkdir(exist_ok=True)
        with (CONTROL / "server.log").open("a", encoding="utf-8") as log:
            subprocess.Popen([sys.executable, str(ROOT / "lab.py"), "hub-server"], cwd=ROOT,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(50):
            time.sleep(0.2)
            config = running_session()
            if config:
                break
    if config:
        webbrowser.open(config["browser_url"], new=2)
    else:
        message = f"Could not start the shared hub. See {CONTROL / 'server.log'}"
        print(message, file=sys.stderr)
        CONTROL.mkdir(exist_ok=True)
        with (CONTROL / "server.log").open("a", encoding="utf-8") as log:
            log.write(message + "\n")


if __name__ == "__main__":
    main()
