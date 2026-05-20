#!/usr/bin/env python3
"""Quest Mass Uploader — push/delete video files on Quest headsets simultaneously over WiFi."""

VERSION = "1.1.7"

import tkinter as tk
from tkinter import ttk
import sv_ttk

from core.adb import find_adb
from core.utils import bundle_dir, resource_dir
from ui.usb_tab import UsbTab
from ui.wifi_tab import WifiTab


class QuestUploader:
    def __init__(self, root, on_back=None):
        self.root = root
        self.on_back = on_back
        self.root.title(f"Quest Mass Uploader  v{VERSION}")
        self.root.geometry("1060x720")
        self.root.minsize(860, 540)
        self.root.state("zoomed")
        self.root.protocol("WM_DELETE_WINDOW",
                           on_back if on_back else self._on_close)

        self.adb_path = find_adb(resource_dir())

        self._setup_ui()
        self._check_adb()

    def _setup_ui(self):
        sv_ttk.set_theme("dark")
        style = ttk.Style()
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("TNotebook.Tab", padding=(12, 5))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        # Header bar
        top = ttk.Frame(self.root, padding=(12, 8))
        top.grid(row=0, column=0, sticky="ew")
        _logo_path = bundle_dir() / "quest_uploader_logo.png"
        if _logo_path.exists():
            self._logo_img = tk.PhotoImage(file=str(_logo_path))
            _h = self._logo_img.height()
            if _h > 40:
                _factor = round(_h / 40)
                self._logo_img = self._logo_img.subsample(_factor, _factor)
            ttk.Label(top, image=self._logo_img).pack(side=tk.LEFT, padx=(0, 10))
        if self.on_back:
            ttk.Button(top, text="← Hub", command=self.on_back,
                       width=8).pack(side=tk.LEFT, padx=(0, 10))
        self.adb_label = ttk.Label(top, text="ADB: Checking...")
        self.adb_label.pack(side=tk.LEFT)
        ttk.Label(top, text=f"v{VERSION}", foreground="#888").pack(side=tk.RIGHT)

        # Tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        usb_frame  = ttk.Frame(self.notebook, padding=10)
        wifi_frame = ttk.Frame(self.notebook)
        self.notebook.add(usb_frame,  text="   USB Setup   ")
        self.notebook.add(wifi_frame, text="   WiFi Upload   ")

        self.usb_tab  = UsbTab(usb_frame, self)
        self.wifi_tab = WifiTab(wifi_frame, self)

    def _check_adb(self):
        if self.adb_path:
            self.adb_label.config(text="ADB: Ready", foreground="#00bc8c")
        else:
            self.adb_label.config(
                text="ADB not found — place the ADB folder next to this script",
                foreground="#e74c3c",
            )

    def _on_close(self):
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    _icon = bundle_dir() / "icon.ico"
    if _icon.exists():
        root.iconbitmap(str(_icon))
    app = QuestUploader(root)
    root.mainloop()
