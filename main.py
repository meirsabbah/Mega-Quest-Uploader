#!/usr/bin/env python3
"""NitzFlash — unified tool hub."""

VERSION = "2.1.1"

import tkinter as tk
from tkinter import ttk
from pathlib import Path
import sv_ttk

from core.utils import bundle_dir, resource_dir

try:
    from PIL import Image, ImageTk
    _PIL = True
except ImportError:
    _PIL = False


class NitzFlash:
    def __init__(self, root):
        self.root = root
        self._images = []   # keep PhotoImage refs alive
        self._show_hub()

    # ------------------------------------------------------------------
    # Hub page
    # ------------------------------------------------------------------

    def _show_hub(self):
        self._images = []
        for w in self.root.winfo_children():
            w.destroy()
        sv_ttk.set_theme("dark")
        self.root.title("NitzFlash")
        self.root.geometry("560x380")
        self.root.resizable(False, False)
        self.root.minsize(0, 0)
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self._build_hub()

    def _build_hub(self):
        style = ttk.Style()
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("Card.TLabelframe.Label", font=("Segoe UI", 11, "bold"))

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        # ── Header ──────────────────────────────────────────────────────
        top = ttk.Frame(self.root, padding=(20, 14, 20, 10))
        top.grid(row=0, column=0, sticky="ew")

        _logo = self._load_image(bundle_dir() / "icon.ico", size=96)
        if _logo:
            ttk.Label(top, image=_logo).pack(side=tk.LEFT, padx=(0, 18))

        hf = ttk.Frame(top)
        hf.pack(side=tk.LEFT)
        ttk.Label(hf, text="NitzFlash",
                  font=("Segoe UI", 30, "bold")).pack(anchor="w")
        ttk.Label(hf, text="Choose a tool to launch",
                  foreground="#888", font=("Segoe UI", 11)).pack(anchor="w")

        ttk.Separator(self.root, orient=tk.HORIZONTAL).grid(
            row=0, column=0, sticky="ews", padx=20)

        # ── Tool cards ──────────────────────────────────────────────────
        cards = ttk.Frame(self.root, padding=(20, 14, 20, 20))
        cards.grid(row=1, column=0, sticky="nsew")
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)

        self._make_card(
            cards, col=0,
            title="Quest Mass Uploader",
            desc="Bulk upload, delete & manage\nfiles on Meta Quest VR headsets",
            command=self._launch_quest,
        )
        self._make_card(
            cards, col=1,
            title="Video Encrypter",
            desc="Encrypt videos and write them\nto USB drives securely",
            command=self._launch_encrypter,
        )

    def _make_card(self, parent, col, title, desc, command):
        card = ttk.LabelFrame(parent, text=title, padding=16,
                              style="Card.TLabelframe")
        card.grid(row=0, column=col, sticky="nsew",
                  padx=(0, 10) if col == 0 else (10, 0))
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text=desc, foreground="#aaa",
                  justify=tk.CENTER, font=("Segoe UI", 9)).pack(expand=True)
        ttk.Button(card, text="Launch", command=command,
                   style="Accent.TButton", width=14).pack(pady=(10, 0))

    # ------------------------------------------------------------------
    # Image loader
    # ------------------------------------------------------------------

    def _load_image(self, path, size=48):
        path = Path(path)
        if not path.exists():
            return None
        try:
            if _PIL:
                img = Image.open(str(path)).convert("RGBA")
                img.thumbnail((size, size), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
            else:
                photo = tk.PhotoImage(file=str(path))
                h = photo.height()
                if h > size:
                    factor = round(h / size)
                    photo = photo.subsample(factor, factor)
            self._images.append(photo)
            return photo
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Launchers — single window, no Toplevel
    # ------------------------------------------------------------------

    def _launch_quest(self):
        for w in self.root.winfo_children():
            w.destroy()
        self.root.resizable(True, True)
        from uploader import QuestUploader
        QuestUploader(self.root, on_back=self._show_hub)

    def _launch_encrypter(self):
        for w in self.root.winfo_children():
            w.destroy()
        self.root.resizable(True, True)
        from video_encrypter.setup_usb import VideoEncrypterApp
        VideoEncrypterApp(self.root, on_back=self._show_hub)


if __name__ == "__main__":
    root = tk.Tk()
    _icon = bundle_dir() / "icon.ico"
    if _icon.exists():
        root.iconbitmap(str(_icon))
    NitzFlash(root)
    root.mainloop()
