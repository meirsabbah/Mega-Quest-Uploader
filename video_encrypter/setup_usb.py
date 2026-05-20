"""
Video Encrypter — encrypt a video file and write it to a USB drive.
Runs inside the NitzFlash hub (plain class, not a Toplevel subclass).
"""

import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import sv_ttk

try:
    from PIL import Image, ImageTk
    _PIL = True
except ImportError:
    _PIL = False

# ---------------------------------------------------------------------------
# Encryption key — must match client_player.py
# ---------------------------------------------------------------------------
ENCRYPTION_KEY = bytes.fromhex(
    "a3f8e2b1c4d5e6f708192a3b4c5d6e7f"
    "809102030405060708090a0b0c0d0e0f"
)

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
    _CRYPTO = True
except ImportError:
    _CRYPTO = False

LIBRARY_DIR   = Path.home() / "VideoEncrypterLibrary"
ENCRYPTED_DIR = LIBRARY_DIR / "encrypted"
LIBRARY_INDEX = LIBRARY_DIR / "library.json"
CHUNK         = 8 * 1024 * 1024   # 8 MB

SYSTEM_NAMES = {"System Volume Information", "$RECYCLE.BIN", "desktop.ini",
                "RECYCLER", "Thumbs.db", "autorun.inf"}

# Brand accent colours (used sparingly on top of sv_ttk dark)
HEADER_BG = "#ffffff"
FG_DARK   = "#0d2054"
GREEN     = "#22c55e"
RED       = "#ef4444"
ORANGE    = "#f97316"
BLUE      = "#1d80c8"
DARK      = "#0a0f1a"   # log console background

# Unicode RTL mark — forces correct right-to-left rendering in tkinter
_R = "‏"


def _resource_path(rel):
    """Locate a file relative to this module (works frozen and from source)."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "video_encrypter", rel)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_removable_drives():
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_LogicalDisk | "
             "Where-Object {$_.DriveType -eq 2} | "
             "Select-Object -ExpandProperty DeviceID"],
            capture_output=True, text=True, timeout=10,
        )
        return [ln.strip() for ln in r.stdout.splitlines()
                if len(ln.strip()) == 2 and ln.strip()[1] == ":"]
    except Exception:
        return []


def drive_is_empty(drive):
    try:
        return all(p.name in SYSTEM_NAMES for p in Path(drive + "\\").iterdir())
    except Exception:
        return False


def hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def encrypt_to(src, dst, on_progress=None):
    if not _CRYPTO:
        raise RuntimeError("cryptography package not installed")
    nonce  = os.urandom(16)
    cipher = Cipher(algorithms.AES(ENCRYPTION_KEY), modes.CTR(nonce),
                    backend=default_backend())
    enc   = cipher.encryptor()
    total = os.path.getsize(src)
    done  = 0
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        fout.write(nonce)
        while True:
            chunk = fin.read(CHUNK)
            if not chunk:
                break
            fout.write(enc.update(chunk))
            done += len(chunk)
            if on_progress:
                on_progress(done / total)
        fout.write(enc.finalize())


def copy_with_progress(src, dst, on_progress=None):
    total = os.path.getsize(src)
    done  = 0
    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        while True:
            chunk = fsrc.read(CHUNK)
            if not chunk:
                break
            fdst.write(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done / total)


def hide_path(path):
    subprocess.run(["attrib", "+H", "+S", str(path)], capture_output=True)


def set_drive_label(drive, label):
    safe   = re.sub(r'[\\/*?:"<>|]', "", label)[:32]
    letter = drive.rstrip(":\\")
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f'Set-Volume -DriveLetter {letter} -NewFileSystemLabel "{safe}"'],
        capture_output=True,
    )


def load_library():
    if LIBRARY_INDEX.exists():
        with open(LIBRARY_INDEX) as f:
            return json.load(f)
    return {}


def save_library(lib):
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    ENCRYPTED_DIR.mkdir(parents=True, exist_ok=True)
    with open(LIBRARY_INDEX, "w") as f:
        json.dump(lib, f, indent=2)


def safe_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip() or "video"


# ---------------------------------------------------------------------------
# Main app class — plain class, runs inside any Tk/Toplevel window
# ---------------------------------------------------------------------------

class VideoEncrypterApp:
    def __init__(self, master=None, on_back=None):
        self.root     = master
        self.on_back  = on_back
        self.root.title("Video Encrypter — מגן און קי")
        self.root.geometry("560x560")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW",
                           on_back if on_back else self.root.destroy)
        self._q        = queue.Queue()
        self._logo_img = None

        self._load_logo()
        self._build_styles()
        self._build_ui()
        self._refresh_drives()
        self.root.after(80, self._pump_queue)

    # ------------------------------------------------------------------
    # Logo
    # ------------------------------------------------------------------

    def _load_logo(self):
        if not _PIL:
            return
        try:
            img = Image.open(_resource_path("logo_white.png")).convert("RGBA")
            img.thumbnail((64, 64), Image.LANCZOS)
            self._logo_img = ImageTk.PhotoImage(img)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Thread-safe UI bridge
    # ------------------------------------------------------------------

    def _pump_queue(self):
        if not self.root.winfo_exists():
            return
        while not self._q.empty():
            try:
                self._q.get_nowait()()
            except Exception:
                pass
        self.root.after(80, self._pump_queue)

    def _ui(self, fn):
        self._q.put(fn)

    # ------------------------------------------------------------------
    # Styles — minimal on top of sv_ttk dark
    # ------------------------------------------------------------------

    def _build_styles(self):
        s = ttk.Style()
        s.configure("Header.TLabel", font=("Segoe UI", 14, "bold"))
        s.configure("Sub.TLabel",    foreground="#94a3b8", font=("Segoe UI", 9))
        s.configure("Hint.TLabel",   foreground="#6b7280", font=("Segoe UI", 9))
        s.configure("Green.TLabel",  foreground=GREEN)
        s.configure("Red.TLabel",    foreground=RED)
        s.configure("Orange.TLabel", foreground=ORANGE)

    # ------------------------------------------------------------------
    # UI layout
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Header
        header = ttk.Frame(self.root, height=80)
        header.pack(fill="x")
        header.pack_propagate(False)

        if self.on_back:
            ttk.Button(header, text="← Hub", command=self.on_back,
                       width=8).pack(side="left", padx=(12, 0), pady=8)

        if self._logo_img:
            ttk.Label(header, image=self._logo_img).pack(
                side="right", padx=(10, 16), pady=8)

        title_frame = ttk.Frame(header)
        title_frame.pack(side="right", pady=8)
        ttk.Label(title_frame, text=f"{_R}מגן און קי",
                  style="Header.TLabel").pack(anchor="e")
        ttk.Label(title_frame, text=f"{_R}הצפנת וידאו לכונן",
                  style="Sub.TLabel").pack(anchor="e")

        # Accent divider
        tk.Frame(self.root, bg=BLUE, height=3).pack(fill="x")

        P = dict(padx=24, pady=5)

        # Drive row
        f1 = ttk.Frame(self.root)
        f1.pack(fill="x", padx=24, pady=(14, 2))
        ttk.Label(f1, text=f"{_R}כונן", width=12, anchor="e",
                  font=("Segoe UI", 10)).pack(side="right")
        self.drive_var   = tk.StringVar()
        self.drive_combo = ttk.Combobox(f1, textvariable=self.drive_var,
                                        width=9, state="readonly")
        self.drive_combo.pack(side="right", padx=6)
        self.drive_combo.bind("<<ComboboxSelected>>", lambda _: self._check_drive())
        self.drive_lbl = ttk.Label(f1, text="", style="Orange.TLabel",
                                   font=("Segoe UI", 10))
        self.drive_lbl.pack(side="left", padx=8)

        self.drive_info_lbl = ttk.Label(self.root, text="", style="Red.TLabel",
                                        anchor="e", wraplength=480,
                                        font=("Segoe UI", 9))
        self.drive_info_lbl.pack(fill="x", padx=24, pady=(0, 4))

        # Video file row
        f2 = ttk.Frame(self.root)
        f2.pack(fill="x", **P)
        ttk.Label(f2, text=f"{_R}קובץ וידאו", width=12, anchor="e",
                  font=("Segoe UI", 10)).pack(side="right")
        self.video_var = tk.StringVar()
        ttk.Entry(f2, textvariable=self.video_var, width=34,
                  justify="right").pack(side="right", padx=6)
        ttk.Button(f2, text=f"{_R}עיון", command=self._browse).pack(side="right")

        # Display name row
        f3 = ttk.Frame(self.root)
        f3.pack(fill="x", **P)
        ttk.Label(f3, text=f"{_R}שם להצגה", width=12, anchor="e",
                  font=("Segoe UI", 10)).pack(side="right")
        # tk.Text instead of ttk.Entry — fixes Hebrew RTL character ordering bug in tkinter
        _s = ttk.Style()
        _ebg = _s.lookup("TEntry", "fieldbackground") or "#1c1c1c"
        _efg = _s.lookup("TEntry", "foreground") or "#ffffff"
        self.title_entry = tk.Text(
            f3, height=1, width=34, wrap="none",
            font=("Segoe UI", 10),
            bg=_ebg, fg=_efg, insertbackground=_efg,
            relief="flat", bd=1,
            selectbackground="#264f78", selectforeground="#ffffff",
        )
        self.title_entry.bind("<Return>", lambda e: "break")
        self.title_entry.pack(side="right", padx=6)
        ttk.Label(f3, text=f"{_R}(מוצג ללקוח)",
                  style="Hint.TLabel").pack(side="left", padx=4)

        # Library status
        self.lib_lbl = ttk.Label(self.root, text="", style="Orange.TLabel",
                                 anchor="e", font=("Segoe UI", 9))
        self.lib_lbl.pack(fill="x", padx=24, pady=2)

        # Progress
        self.prog_lbl = ttk.Label(self.root, text="", anchor="e",
                                  font=("Segoe UI", 9))
        self.prog_lbl.pack(fill="x", padx=24, pady=(10, 3))
        self.progress = ttk.Progressbar(self.root, length=510, mode="determinate")
        self.progress.pack(padx=24)

        # Burn button
        self.burn_btn = ttk.Button(self.root, text=f"{_R}התחל הצפנה",
                                   style="Accent.TButton", command=self._burn)
        self.burn_btn.pack(pady=14)

        # Log console
        self.log = tk.Text(self.root, height=7, bg=DARK, fg="#4ade80",
                           font=("Consolas", 10), state="disabled",
                           relief="flat", bd=0)
        self.log.tag_configure("rtl", justify="right")
        self.log.pack(fill="x", padx=24, pady=(0, 18))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _log(self, msg):
        def _do():
            if not self.root.winfo_exists():
                return
            self.log.configure(state="normal")
            self.log.insert("end", msg + "\n", "rtl")
            self.log.see("end")
            self.log.configure(state="disabled")
        self._ui(_do)

    def _set_progress(self, value, label=""):
        def _do():
            if not self.root.winfo_exists():
                return
            self.progress["value"] = value
            if label:
                self.prog_lbl.config(text=label)
        self._ui(_do)

    def _refresh_drives(self):
        drives = get_removable_drives()
        self.drive_combo["values"] = drives
        if drives:
            self.drive_var.set(drives[0])
            self._check_drive()
        else:
            self.drive_var.set("")
            self.drive_lbl.config(text=f"{_R}לא נמצא כונן", style="Red.TLabel")
            self.drive_info_lbl.config(text="")

    def _check_drive(self):
        d = self.drive_var.get()
        if not d:
            return
        if drive_is_empty(d):
            self.drive_lbl.config(text=f"{_R}מוכן (ריק)", style="Green.TLabel")
            self.drive_info_lbl.config(text="")
        else:
            self.drive_lbl.config(text=f"{_R}לא ריק", style="Red.TLabel")
            self.drive_info_lbl.config(
                text=f"{_R}הכונן אינו ריק. סגור את האפליקציה, פרמט את הכונן, ופתח מחדש."
            )

    def _browse(self):
        path = filedialog.askopenfilename(
            title="בחר קובץ וידאו",
            filetypes=[("קבצי וידאו", "*.mp4 *.mkv *.avi *.mov *.wmv *.m4v *.ts *.flv"),
                       ("כל הקבצים", "*.*")],
        )
        if not path:
            return
        self.video_var.set(path)
        if not self.title_entry.get("1.0", "end-1c").strip():
            self.title_entry.delete("1.0", "end")
            self.title_entry.insert("1.0", Path(path).stem)
        lib = load_library()
        fh  = hash_file(path)
        if fh in lib and Path(lib[fh]["enc"]).exists():
            self.lib_lbl.config(text=f"{_R}נמצא בספרייה — אין צורך בהצפנה מחדש",
                                style="Green.TLabel")
        else:
            self.lib_lbl.config(text=f"{_R}לא בספרייה — יוצפן בעת הכתיבה",
                                style="Orange.TLabel")

    def _burn(self):
        drive = self.drive_var.get()
        video = self.video_var.get()
        title = self.title_entry.get("1.0", "end-1c").strip()
        if not drive:
            messagebox.showerror("שגיאה", "יש לבחור כונן USB.", parent=self.root); return
        if not video or not Path(video).exists():
            messagebox.showerror("שגיאה", "יש לבחור קובץ וידאו תקין.", parent=self.root); return
        if not title:
            messagebox.showerror("שגיאה", "יש להזין שם להצגה עבור הסרטון.", parent=self.root); return
        if not drive_is_empty(drive):
            messagebox.showerror("שגיאה",
                f"הכונן {drive} אינו ריק.\n\n"
                "יש לפרמט אותו (לחיצה ימנית ← פרמט בסייר) ולנסות שוב.",
                parent=self.root); return
        client_exe = self._find_client()
        if not client_exe:
            messagebox.showerror("שגיאה",
                "הקובץ client.exe לא נמצא.\n"
                "יש להניח אותו בתיקייה שבה נמצא NitzFlash.exe.",
                parent=self.root); return

        self.burn_btn.config(state="disabled")
        threading.Thread(target=self._worker,
                         args=(drive, video, title, client_exe), daemon=True).start()

    def _find_client(self):
        from core.utils import resource_dir
        candidates = [
            resource_dir() / "client.exe",
            Path(sys.executable).parent / "client.exe",
            Path(__file__).resolve().parent.parent / "client.exe",
            Path(__file__).resolve().parent / "dist" / "client.exe",
        ]
        for p in candidates:
            if p.exists():
                return p
        return None

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    def _worker(self, drive, video_path, title, client_exe):
        try:
            self._log(f"{_R}מתחיל תהליך כתיבה...")
            lib           = load_library()
            fh            = hash_file(video_path)
            original_name = Path(video_path).name
            ext           = Path(video_path).suffix

            cached = fh in lib and Path(lib[fh]["enc"]).exists()
            if cached:
                enc_path = Path(lib[fh]["enc"])
                self._log(f"{_R}נמצא בספרייה — משתמש ב-{enc_path.name}")
            else:
                ENCRYPTED_DIR.mkdir(parents=True, exist_ok=True)
                enc_path = ENCRYPTED_DIR / f"{fh[:16]}.enc"
                self._log(f"{_R}מצפין וידאו (עשוי לקחת זמן)...")

                def ep(pct):
                    self._set_progress(pct * 65, f"{_R}מצפין... {int(pct*100)}%")

                encrypt_to(video_path, enc_path, ep)
                lib[fh] = {"enc": str(enc_path), "name": original_name}
                save_library(lib)
                self._log(f"{_R}הוצפן ונשמר בספרייה: {enc_path.name}")

            set_drive_label(drive, title)
            self._log(f"{_R}שם הכונן עודכן: {title}")
            self._log(f"{_R}מעתיק קבצים לכונן...")
            media        = Path(drive + "\\") / "media"
            media.mkdir(exist_ok=True)
            enc_filename = safe_filename(title) + ".enc"
            dst_enc      = media / enc_filename
            dst_meta     = media / "meta.txt"
            exe_name     = safe_filename(title) + ".exe"
            dst_client   = Path(drive + "\\") / exe_name

            copy_start = 65 if not cached else 0

            def cp(pct):
                self._set_progress(copy_start + pct * (95 - copy_start),
                                   f"{_R}מעתיק לכונן... {int(pct*100)}%")

            copy_with_progress(enc_path, dst_enc, cp)
            dst_meta.write_text(f"{title}\n{ext}", encoding="utf-8")
            shutil.copy2(client_exe, dst_client)
            self._log(f"{_R}הקבצים הועתקו.")

            hide_path(dst_enc)
            hide_path(dst_meta)
            hide_path(media)
            self._log(f"{_R}תיקיית המדיה הוסתרה.")

            self._set_progress(100, f"{_R}הסתיים!")
            self._log(f"{_R}הכתיבה הושלמה! הכונן מוכן.")
            self._ui(lambda: messagebox.showinfo(
                "הסתיים",
                f"הכונן {drive} מוכן!\n\n"
                f"הלקוח לוחץ פעמיים על:\n{exe_name}\n\n"
                f"קובץ מוצפן:\n{enc_filename}",
                parent=self.root,
            ))
        except Exception as e:
            msg = str(e)
            if "28" in msg or "space" in msg.lower() or "no space" in msg.lower():
                msg = (
                    "Not enough space on the USB drive.\n\n"
                    "If the drive has free space but still fails, it is probably\n"
                    "formatted as FAT32 which has a 4 GB per-file limit.\n\n"
                    "Fix: right-click the drive in Explorer → Format → exFAT → Start."
                )
            self._log(f"{_R}שגיאה: {e}")
            self._ui(lambda m=msg: messagebox.showerror("Write Error", m, parent=self.root))
        finally:
            self._ui(lambda: self.burn_btn.config(state="normal")
                     if self.root.winfo_exists() else None)
