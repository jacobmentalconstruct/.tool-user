"""Run a visible app window and accept live prompts from stdin or a JSONL inbox.

The app's own Send path handles prompts, and chat events are mirrored to stdout
or a JSONL transcript for the driver to observe without scraping the window.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk

from .desktop import App


class MonitoredApp(App):
    transcript: Path | None = None

    def _append(self, speaker: str, message: str, tag: str):
        super()._append(speaker, message, tag)
        print(f"[{speaker}] {message}", flush=True)
        if self.transcript is not None:
            with self.transcript.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"speaker": speaker, "text": message}, ensure_ascii=False) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--inbox", type=Path, help="JSONL prompt inbox for a visible desktop process")
    parser.add_argument("--transcript", type=Path, help="JSONL copy of visible chat events")
    args = parser.parse_args(argv)
    commands: queue.Queue[str] = queue.Queue()
    root = tk.Tk()
    app = MonitoredApp(root)
    app.transcript = args.transcript
    root.title("Local Memory Lab · Shared Live Session")

    def read_prompts():
        for line in sys.stdin:
            prompt = line.strip()
            if prompt:
                commands.put(prompt)

    def deliver():
        if not app.busy:
            try:
                prompt = commands.get_nowait()
            except queue.Empty:
                pass
            else:
                app.input.delete(0, "end")
                app.input.insert(0, prompt)
                app._send()
        root.after(100, deliver)

    if args.inbox is None:
        threading.Thread(target=read_prompts, daemon=True).start()
    else:
        args.inbox.parent.mkdir(parents=True, exist_ok=True)
        args.inbox.touch(exist_ok=True)
        offset = args.inbox.stat().st_size

        def read_inbox():
            nonlocal offset
            with args.inbox.open("r", encoding="utf-8") as stream:
                stream.seek(offset)
                while line := stream.readline():
                    try:
                        item = json.loads(line)
                        prompt = item.get("prompt", "")
                        if isinstance(prompt, str) and prompt.strip():
                            commands.put(prompt.strip())
                    except (ValueError, AttributeError):
                        pass
                offset = stream.tell()
            root.after(250, read_inbox)

        root.after(250, read_inbox)
    root.after(100, deliver)
    root.deiconify()
    root.lift()
    root.attributes("-topmost", True)
    root.focus_force()
    root.after(3000, lambda: root.attributes("-topmost", False))
    print("READY: visible Live Session window is open; enter one prompt per line.", flush=True)
    root.mainloop()


if __name__ == "__main__":
    main()
