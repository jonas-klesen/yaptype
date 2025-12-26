import argparse
import os
import sys
import tkinter as tk
from tkinter import ttk

from control import SOCKET_PATH, send_command


BG = "#0f1115"
PANEL_BG = "#141824"
FG = "#e6e6e6"
MUTED = "#a8b0c2"
BTN_BG = "#1d2333"
BTN_ACTIVE_BG = "#2a3248"

POLL_MS = 150


class TailGuiApp:
    def __init__(self, root: tk.Tk, *, filepath: str, socket_path: str) -> None:
        self.root = root
        self.filepath = filepath
        self.socket_path = socket_path
        self._offset = 0

        root.title("YapType — Live Transcript")
        root.configure(background=BG)
        root.geometry("900x650")

        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except Exception:  # noqa: BLE001
            pass

        style.configure("App.TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL_BG)
        style.configure("App.TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure(
            "App.TButton",
            background=BTN_BG,
            foreground=FG,
            borderwidth=0,
            padding=(10, 10),
        )
        style.map("App.TButton", background=[("active", BTN_ACTIVE_BG)])

        container = ttk.Frame(root, style="App.TFrame")
        container.grid(row=0, column=0, sticky="nsew")
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)

        header = ttk.Frame(container, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 8))
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="Tailing:", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text=self.filepath, style="App.TLabel").grid(row=1, column=0, sticky="w")

        panel = ttk.Frame(container, style="Panel.TFrame")
        panel.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 12))
        container.rowconfigure(1, weight=1)
        container.columnconfigure(0, weight=1)

        self.text = tk.Text(
            panel,
            wrap="word",
            bg=BG,
            fg=FG,
            insertbackground=FG,
            selectbackground="#2e3954",
            selectforeground=FG,
            bd=0,
            highlightthickness=0,
            padx=14,
            pady=12,
            undo=True,
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        panel.rowconfigure(0, weight=1)
        panel.columnconfigure(0, weight=1)

        scrollbar = ttk.Scrollbar(panel, orient="vertical", command=self.text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scrollbar.set)

        buttons = ttk.Frame(container, style="App.TFrame")
        buttons.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 14))
        for i in range(3):
            buttons.columnconfigure(i, weight=1, uniform="btn")

        ttk.Button(buttons, text="Stop", style="App.TButton", command=self._on_stop).grid(
            row=0, column=0, sticky="ew", padx=(0, 8)
        )
        ttk.Button(buttons, text="Copy", style="App.TButton", command=self._on_copy).grid(
            row=0, column=1, sticky="ew", padx=8
        )
        ttk.Button(buttons, text="Exit", style="App.TButton", command=self._on_exit).grid(
            row=0, column=2, sticky="ew", padx=(8, 0)
        )

        self._load_initial()
        self._poll()

    def _load_initial(self) -> None:
        if not os.path.exists(self.filepath):
            self.text.insert("1.0", "")
            return
        try:
            data = self._read_all()
            self.text.insert("1.0", data)
            self.text.see("end")
        except Exception:  # noqa: BLE001
            pass

    def _read_all(self) -> str:
        with open(self.filepath, "rb") as f:
            data = f.read()
        self._offset = len(data)
        return data.decode("utf-8", errors="replace")

    def _read_new(self) -> str:
        try:
            size = os.path.getsize(self.filepath)
        except FileNotFoundError:
            return ""

        if size < self._offset:
            self._offset = 0

        if size == self._offset:
            return ""

        with open(self.filepath, "rb") as f:
            f.seek(self._offset)
            data = f.read()
        self._offset += len(data)
        return data.decode("utf-8", errors="replace")

    def _append_text(self, chunk: str) -> None:
        if not chunk:
            return

        at_bottom = self.text.yview()[1] >= 0.999
        insert_pos = self.text.index("insert")

        self.text.insert("end", chunk)

        if at_bottom:
            self.text.see("end")
        else:
            self.text.mark_set("insert", insert_pos)

    def _poll(self) -> None:
        try:
            chunk = self._read_new()
            if chunk:
                self._append_text(chunk)
        finally:
            self.root.after(POLL_MS, self._poll)

    def _on_stop(self) -> None:
        try:
            send_command("STOP", socket_path=self.socket_path)
        except Exception:  # noqa: BLE001
            pass

    def _on_copy(self) -> None:
        content = self.text.get("1.0", "end-1c")
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.root.update_idletasks()

    def _on_exit(self) -> None:
        self.root.destroy()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("filepath")
    parser.add_argument("--socket-path", default=SOCKET_PATH)
    args = parser.parse_args(argv)

    root = tk.Tk()
    TailGuiApp(root, filepath=args.filepath, socket_path=args.socket_path)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

