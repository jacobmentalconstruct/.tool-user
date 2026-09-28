"""Small, session-only Ollama chat with bounded local file tools."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

from ..agent.engine import DEFAULT_MODEL, MAX_RECENT_TURNS, installed_chat_models, run_turn
from ..agent.file_tools import FileTools
from ..locations import OUTPUT


OUTPUT_DIR = OUTPUT

BG = "#171a20"
PANEL = "#22262e"
FIELD = "#2d333d"
TEXT = "#f1f3f5"
MUTED = "#a9b1bc"
ACCENT = "#7db6ff"
BUTTON = "#365b89"

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Local Memory Lab · File Tools")
        self.root.geometry("920x680")
        self.root.minsize(720, 500)
        self.root.configure(bg=BG)
        self.turns: list[list[dict]] = []
        self.notes: list[str] = []
        self.file_tools = FileTools(OUTPUT_DIR)
        self.events: queue.Queue[tuple] = queue.Queue()
        self.busy = False
        self.model = tk.StringVar(value=DEFAULT_MODEL)
        self.status = tk.StringVar(value="Checking Ollama…")
        self._build_ui()
        self.root.after(50, self._poll_events)
        threading.Thread(target=self._load_models, daemon=True).start()

    def _build_ui(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Dark.TCombobox", fieldbackground=FIELD, background=FIELD, foreground=TEXT,
                        arrowcolor=TEXT, bordercolor=FIELD)
        style.map("Dark.TCombobox", fieldbackground=[("readonly", FIELD)],
                  foreground=[("readonly", TEXT)])

        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=18, pady=(15, 8))
        tk.Label(top, text="LOCAL MEMORY LAB", bg=BG, fg=TEXT,
                 font=("Segoe UI", 16, "bold")).pack(side="left")
        tk.Label(top, text="Model", bg=BG, fg=MUTED).pack(side="left", padx=(24, 8))
        self.model_box = ttk.Combobox(top, textvariable=self.model, width=23,
                                      style="Dark.TCombobox", state="readonly")
        self.model_box["values"] = [DEFAULT_MODEL]
        self.model_box.pack(side="left")
        tk.Label(top, textvariable=self.status, bg=BG, fg=ACCENT).pack(side="right")

        body = tk.PanedWindow(self.root, orient="horizontal", bg=BG, sashwidth=5,
                              showhandle=False, bd=0)
        body.pack(fill="both", expand=True, padx=18, pady=8)
        chat_side = tk.Frame(body, bg=PANEL)
        notes_side = tk.Frame(body, bg=PANEL, width=250)
        body.add(chat_side, minsize=420, stretch="always")
        body.add(notes_side, minsize=210)

        tk.Label(chat_side, text="Chat · short term memory", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=12, pady=(12, 6))
        self.chat = tk.Text(chat_side, wrap="word", state="disabled", bg=PANEL, fg=TEXT,
                            insertbackground=TEXT, relief="flat", padx=12, pady=8,
                            font=("Segoe UI", 10), selectbackground=BUTTON)
        self.chat.pack(fill="both", expand=True)
        self.chat.tag_configure("you", foreground=ACCENT, font=("Segoe UI", 10, "bold"))
        self.chat.tag_configure("assistant", foreground="#9de0b0", font=("Segoe UI", 10, "bold"))
        self.chat.tag_configure("tool", foreground="#e4c884", font=("Segoe UI", 10, "bold"))
        self.chat.tag_configure("error", foreground="#ff9c9c")
        self.input = tk.Entry(chat_side, bg=FIELD, fg=TEXT, insertbackground=TEXT,
                              relief="flat", font=("Segoe UI", 11))
        self.input.pack(fill="x", padx=12, pady=(6, 8), ipady=8)
        self.input.bind("<Return>", self._send)
        self.send_button = self._button(chat_side, "Send", self._send)
        self.send_button.pack(anchor="e", padx=12, pady=(0, 12))

        tk.Label(notes_side, text="Long term notes · this run", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=12, pady=(12, 6))
        tk.Label(notes_side, text="Add facts for the model to remember until you close the app.",
                 bg=PANEL, fg=MUTED, wraplength=225, justify="left").pack(anchor="w", padx=12)
        self.note_input = tk.Entry(notes_side, bg=FIELD, fg=TEXT, insertbackground=TEXT,
                                   relief="flat", font=("Segoe UI", 10))
        self.note_input.pack(fill="x", padx=12, pady=(12, 8), ipady=7)
        self.note_input.bind("<Return>", self._add_note)
        self._button(notes_side, "Remember", self._add_note).pack(anchor="e", padx=12)
        self.note_list = tk.Listbox(notes_side, bg=FIELD, fg=TEXT, selectbackground=BUTTON,
                                    relief="flat", highlightthickness=0, font=("Segoe UI", 10))
        self.note_list.pack(fill="both", expand=True, padx=12, pady=12)
        self._button(notes_side, "Forget selected", self._forget_note).pack(anchor="e", padx=12, pady=(0, 12))
        tk.Label(self.root, text=f"Tool output: {OUTPUT_DIR}   •   Memory clears when this window closes",
                 bg=BG, fg=MUTED, anchor="w").pack(fill="x", padx=18, pady=(0, 12))
        self.input.focus_set()

    def _button(self, parent, label, command):
        return tk.Button(parent, text=label, command=command, bg=BUTTON, fg=TEXT,
                         activebackground=ACCENT, activeforeground=BG, relief="flat",
                         padx=12, pady=5, cursor="hand2")

    def _append(self, speaker: str, message: str, tag: str):
        self.chat.configure(state="normal")
        self.chat.insert("end", f"{speaker}\n", tag)
        self.chat.insert("end", message + "\n\n", "error" if tag == "error" else "")
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def _load_models(self):
        try:
            self.events.put(("models", installed_chat_models()))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _add_note(self, event=None):
        note = self.note_input.get().strip()
        if note:
            self.notes.append(note)
            self.note_list.insert("end", note)
            self.note_input.delete(0, "end")

    def _forget_note(self):
        selection = self.note_list.curselection()
        if selection:
            index = selection[0]
            del self.notes[index]
            self.note_list.delete(index)

    def _send(self, event=None):
        prompt = self.input.get().strip()
        if not prompt or self.busy:
            return
        self.input.delete(0, "end")
        self._append("You", prompt, "you")
        self.busy = True
        self.send_button.configure(state="disabled")
        self.status.set("Thinking…")
        threading.Thread(target=self._chat_worker, args=(prompt, self.model.get(), list(self.notes)),
                         daemon=True).start()

    def _chat_worker(self, prompt: str, model: str, notes: list[str]):
        try:
            answer, turn = run_turn(prompt, model, self.turns, notes, self.file_tools,
                                    self._confirm_overwrite,
                                    lambda result: self.events.put(("tool", result["message"])))
            self.events.put(("answer", answer, turn))
        except Exception as exc:
            self.events.put(("error", str(exc)))
            self.events.put(("done",))

    def _confirm_overwrite(self, name: str, old_content: str, new_content: str) -> bool:
        reply = threading.Event()
        decision = [False]
        self.events.put(("confirm", name, old_content, new_content, reply, decision))
        reply.wait()
        return decision[0]

    def _show_confirmation(self, name: str, old_content: str, new_content: str,
                           reply: threading.Event, decision: list[bool]):
        dialog = tk.Toplevel(self.root)
        dialog.title("Confirm overwrite")
        dialog.configure(bg=PANEL)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        tk.Label(dialog, text="This file already exists", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 12, "bold")).pack(padx=22, pady=(20, 8))
        tk.Label(dialog, text=f"Overwrite {name}? Review the contents below.", bg=PANEL, fg=MUTED,
                 wraplength=580).pack(padx=22, pady=(0, 12))
        previews = tk.Frame(dialog, bg=PANEL)
        previews.pack(fill="both", expand=True, padx=18)
        for heading, value in (("Existing", old_content), ("Proposed", new_content)):
            column = tk.Frame(previews, bg=PANEL)
            column.pack(side="left", fill="both", expand=True, padx=5)
            tk.Label(column, text=heading, bg=PANEL, fg=ACCENT, anchor="w").pack(fill="x")
            box = tk.Text(column, wrap="word", bg=FIELD, fg=TEXT, relief="flat",
                          font=("Consolas", 9), width=35, height=13, padx=8, pady=8)
            box.pack(fill="both", expand=True)
            box.insert("1.0", value[:4000] + ("\n… (preview truncated)" if len(value) > 4000 else ""))
            box.configure(state="disabled")
        row = tk.Frame(dialog, bg=PANEL)
        row.pack(pady=(16, 20))

        def finish(overwrite: bool):
            decision[0] = overwrite
            dialog.grab_release()
            dialog.destroy()
            reply.set()

        self._button(row, "Cancel", lambda: finish(False)).pack(side="left", padx=6)
        self._button(row, "Overwrite", lambda: finish(True)).pack(side="left", padx=6)
        dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        dialog.bind("<Escape>", lambda event: finish(False))
        dialog.update_idletasks()
        dialog.geometry("650x430")
        dialog.geometry(f"+{self.root.winfo_rootx() + 220}+{self.root.winfo_rooty() + 180}")
        dialog.focus_force()

    def _poll_events(self):
        try:
            while True:
                item = self.events.get_nowait()
                kind = item[0]
                if kind == "models":
                    names = item[1]
                    self.model_box["values"] = names or [DEFAULT_MODEL]
                    if names and self.model.get() not in names:
                        self.model.set("qwen2.5:7b" if "qwen2.5:7b" in names else names[0])
                    self.status.set("Ollama ready" if names else "No models found")
                elif kind == "confirm":
                    self._show_confirmation(*item[1:])
                elif kind == "tool":
                    self._append("Tool", item[1], "tool")
                elif kind == "answer":
                    self.turns.append(item[2])
                    self.turns = self.turns[-MAX_RECENT_TURNS:]
                    self._append("Assistant", item[1], "assistant")
                    self.busy = False
                    self.send_button.configure(state="normal")
                    self.status.set("Ollama ready")
                elif kind == "error":
                    self._append("Error", item[1], "error")
                    self.status.set("Check Ollama")
                elif kind == "done":
                    self.busy = False
                    self.send_button.configure(state="normal")
        except queue.Empty:
            pass
        self.root.after(50, self._poll_events)


def main():
    App(tk.Tk()).root.mainloop()


if __name__ == "__main__":
    main()
