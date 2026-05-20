"""
מגן און קי — ניצחה הרוח
נגן וידאו לכונן USB
"""

import os, sys, tempfile, threading, queue
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

# ── Must match setup_usb.py ───────────────────────────────────────────────────
ENCRYPTION_KEY = bytes.fromhex(
    "a3f8e2b1c4d5e6f708192a3b4c5d6e7f"
    "809102030405060708090a0b0c0d0e0f"
)

CHUNK = 8 * 1024 * 1024   # 8 MB

# ── Brand colours ─────────────────────────────────────────────────────────────
BG    = "#ffffff"
FG    = "#0d2054"
BLUE  = "#1d80c8"
LIGHT = "#e8f2fb"
GREY  = "#6b7280"


def resource_path(rel):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, rel)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)


def find_media():
    base      = Path(sys.executable).parent
    meta_path = base / "media" / "meta.txt"
    if not meta_path.exists():
        return None, None, None

    lines = meta_path.read_text(encoding="utf-8").splitlines()
    title = lines[0].strip() if len(lines) >= 1 else "וידאו"
    ext   = lines[1].strip() if len(lines) >= 2 else ".mp4"
    if not ext.startswith("."):
        ext = "." + ext

    enc_files = sorted((base / "media").glob("*.enc"))
    if not enc_files:
        return None, None, None

    return enc_files[0], ext, title


def decrypt_to_temp(enc_path, ext, on_progress=None):
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext)
    os.close(tmp_fd)

    enc_size  = os.path.getsize(enc_path)
    nonce_len = 16
    total     = max(enc_size - nonce_len, 1)
    done      = 0

    with open(enc_path, "rb") as fin:
        nonce  = fin.read(nonce_len)
        cipher = Cipher(algorithms.AES(ENCRYPTION_KEY), modes.CTR(nonce),
                        backend=default_backend())
        dec    = cipher.decryptor()
        with open(tmp_path, "wb") as fout:
            while True:
                chunk = fin.read(CHUNK)
                if not chunk:
                    break
                fout.write(dec.update(chunk))
                done += len(chunk)
                if on_progress:
                    on_progress(done / total)
            fout.write(dec.finalize())

    return tmp_path


class PlayerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("מגן און קי")
        self.geometry("400x310")
        self.resizable(False, False)
        self.configure(bg=BG)
        self._q        = queue.Queue()
        self._tmp_path = None
        self._logo_img = None
        self._icon_img = None

        self._load_logo()
        self._build_styles()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._pump_queue)
        self.after(300, self._start)

    def _load_logo(self):
        try:
            img = Image.open(resource_path("logo.png")).convert("RGBA")
            img.thumbnail((110, 110), Image.LANCZOS)
            self._logo_img = ImageTk.PhotoImage(img)
            icon = Image.open(resource_path("logo.png")).convert("RGBA")
            icon.thumbnail((32, 32), Image.LANCZOS)
            self._icon_img = ImageTk.PhotoImage(icon)
            self.iconphoto(True, self._icon_img)
        except Exception:
            pass

    # ── Thread-safe UI ────────────────────────────────────────────────────────

    def _pump_queue(self):
        while not self._q.empty():
            try:
                self._q.get_nowait()()
            except Exception:
                pass
        self.after(80, self._pump_queue)

    def _ui(self, fn):
        self._q.put(fn)

    # ── Styles ────────────────────────────────────────────────────────────────

    def _build_styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame",       background=BG)
        s.configure("TLabel",       background=BG, foreground=FG,
                    font=("Segoe UI", 10))
        s.configure("Title.TLabel", background=BG, foreground=FG,
                    font=("Segoe UI", 12, "bold"))
        s.configure("Sub.TLabel",   background=BG, foreground=GREY,
                    font=("Segoe UI", 9))
        s.configure("TButton",      background=BLUE, foreground="white",
                    padding=(14, 6), font=("Segoe UI", 10, "bold"))
        s.map("TButton", background=[("active", "#1565a8")])
        s.configure("TProgressbar", troughcolor=LIGHT, background=BLUE, thickness=10)

    # ── UI layout ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        if self._logo_img:
            tk.Label(self, image=self._logo_img, bg=BG).pack(pady=(20, 6))
        else:
            ttk.Label(self, text="מגן און קי", style="Title.TLabel").pack(pady=(32, 6))

        self.title_lbl = ttk.Label(self, text="", style="Title.TLabel")
        self.title_lbl.pack(pady=(0, 2))

        self.status_lbl = ttk.Label(self, text="אנא המתן...", style="Sub.TLabel")
        self.status_lbl.pack()

        self.progress = ttk.Progressbar(self, length=320, mode="determinate")
        self.progress.pack(pady=(10, 4))

        self.pct_lbl = ttk.Label(self, text="", style="Sub.TLabel")
        self.pct_lbl.pack()

    # ── Playback flow ─────────────────────────────────────────────────────────

    def _set_status(self, video_title="", sub="", pct=None):
        def _do():
            if video_title:
                self.title_lbl.config(text=video_title)
            self.status_lbl.config(text=sub)
            if pct is not None:
                self.progress["value"] = pct * 100
                self.pct_lbl.config(text=f"{int(pct*100)}%")
        self._ui(_do)

    def _start(self):
        enc_path, ext, title = find_media()
        if not enc_path:
            messagebox.showerror(
                "לא נמצא סרטון",
                "קובץ הוידאו לא נמצא בכונן זה.\nאנא פנה למארגן האירוע."
            )
            self.destroy()
            return
        threading.Thread(target=self._decrypt_and_play,
                         args=(enc_path, ext, title), daemon=True).start()

    def _decrypt_and_play(self, enc_path, ext, title):
        try:
            import shutil as _shutil
            enc_size = os.path.getsize(enc_path)
            free     = _shutil.disk_usage(tempfile.gettempdir()).free
            if free < enc_size * 1.05:
                needed = enc_size // (1024 * 1024)
                self._ui(lambda: messagebox.showerror(
                    "אין מספיק מקום",
                    f"נדרש ~{needed} MB פנוי בכונן המערכת.\nיש לפנות מקום ולנסות שוב."
                ))
                self._ui(self.destroy)
                return

            self._set_status(title, "מפענח, אנא המתן...", 0)
            self._tmp_path = decrypt_to_temp(
                enc_path, ext,
                lambda p: self._set_status(title, f"מפענח... {int(p*100)}%", p)
            )
            self._set_status(title, "פותח נגן...", 1.0)
            self._ui(lambda: self._show_playing(title))
            os.startfile(self._tmp_path)
            self._ui(lambda: self.after(30_000, self._try_cleanup))

        except Exception as e:
            self._ui(lambda: messagebox.showerror("שגיאת ניגון", str(e)))
            self._ui(self._cleanup_and_exit)

    def _show_playing(self, title):
        for w in self.winfo_children():
            w.destroy()
        self.geometry("400x270")

        if self._logo_img:
            tk.Label(self, image=self._logo_img, bg=BG).pack(pady=(20, 8))

        ttk.Label(self, text="מתנגן כעת", style="Sub.TLabel").pack()
        ttk.Label(self, text=title, style="Title.TLabel").pack(pady=(4, 20))
        ttk.Button(self, text="סיום — סגור", command=self._on_close).pack()

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def _try_cleanup(self):
        if self._tmp_path and os.path.exists(self._tmp_path):
            try:
                os.unlink(self._tmp_path)
                self._tmp_path = None
            except PermissionError:
                self.after(30_000, self._try_cleanup)

    def _cleanup_and_exit(self):
        self._try_cleanup()
        self.destroy()

    def _on_close(self):
        self._try_cleanup()
        self.destroy()


if __name__ == "__main__":
    app = PlayerApp()
    app.mainloop()
