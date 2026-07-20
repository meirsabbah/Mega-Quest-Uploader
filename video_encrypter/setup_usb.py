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
from tkinter import filedialog, messagebox, simpledialog, ttk
import sv_ttk

try:
    from PIL import Image, ImageTk
    _PIL = True
except ImportError:
    _PIL = False

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    from cryptography.hazmat.backends import default_backend
    _CRYPTO = True
except ImportError:
    _CRYPTO = False

# ---------------------------------------------------------------------------
# Access code + encryption key — salt/KDF must match client_player.py
# ---------------------------------------------------------------------------
# ACCESS_CODE lives ONLY in this file. setup_usb.py stays on the operator's
# PC and is never shipped to a customer — client_player.py is what ends up
# on the USB key (inside client.exe), and it contains no copy of this
# string, no hash of it, no verifier derived from it. It only has the KDF
# algorithm and salt below (public parameters — fine to expose) and derives
# whatever key results from whatever the customer types in. A wrong guess
# there has nothing to check itself against except a real video failing to
# decrypt into something playable.
#
# To rotate the code: change the string below, then rebuild client.exe via
# build.bat. Only affects future burns — already-burned drives keep their
# own client.exe and keep working with whatever code they shipped with.
ACCESS_CODE = "*181818*"

# Public KDF salt — not secret, just fixed so derivation is reproducible.
# Must match client_player.py.
_KDF_SALT = bytes.fromhex(
    "a3f8e2b1c4d5e6f708192a3b4c5d6e7f"
    "809102030405060708090a0b0c0d0e0f"
)


def derive_key(password):
    """scrypt is deliberately slow and memory-hard (~0.3s, ~128MB per
    attempt on typical hardware) so brute-forcing the access code offline
    is expensive, not just inconvenient. Must match client_player.py."""
    kdf = Scrypt(salt=_KDF_SALT, length=32, n=2 ** 17, r=8, p=1)
    return kdf.derive(password.encode("utf-8"))


ENCRYPTION_KEY = derive_key(ACCESS_CODE) if _CRYPTO else None
# Non-secret fingerprint of the current key, stored alongside cached local
# encryptions so rotating ACCESS_CODE automatically invalidates them instead
# of silently reusing ciphertext from the old key on a new burn.
_KEY_FP = hashlib.sha256(ENCRYPTION_KEY).hexdigest()[:16] if _CRYPTO else None

LIBRARY_DIR   = Path.home() / "VideoEncrypterLibrary"
ENCRYPTED_DIR = LIBRARY_DIR / "encrypted"
LIBRARY_INDEX = LIBRARY_DIR / "library.json"
PRESETS_FILE  = LIBRARY_DIR / "presets.json"
CHUNK         = 8 * 1024 * 1024   # 8 MB

# Bundled under an ASCII name (video_encrypter/instructions.pdf) because
# build.bat / the PyInstaller spec pass this path through cmd.exe, which
# reads non-ASCII text using the console's OEM code page and corrupts
# Hebrew filenames before PyInstaller ever sees them. The Hebrew name below
# is only ever used as a plain Python file write onto the USB drive, which
# has no such encoding problem.
INSTRUCTIONS_PDF_RESOURCE = "instructions.pdf"
INSTRUCTIONS_PDF_NAME     = "הוראות_הפעלה.pdf"

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


def copy_client_tree(client_dir, drive_root, exe_name):
    """Copy the onedir client build (client.exe + vlc_runtime + _internal)
    to the USB root, renaming only the top-level exe to the customer's title.
    Returns the list of sibling support-folder paths written (to be hidden).
    """
    shutil.copy2(client_dir / "client.exe", drive_root / exe_name)
    support_dirs = []
    for entry in client_dir.iterdir():
        if entry.name == "client.exe":
            continue
        dst = drive_root / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dst, dirs_exist_ok=True)
            support_dirs.append(dst)
        else:
            shutil.copy2(entry, dst)
    return support_dirs


def hide_path(path):
    subprocess.run(["attrib", "+H", "+S", str(path)], capture_output=True)


def set_drive_label(drive, label):
    """Set the USB volume label. Set-Volume caps FAT32/exFAT labels at 11
    characters and fails the whole call (no volume change) if given more —
    previously this failure was discarded, so any longer event name left
    the drive with its default generic name. Returns (success, label_used)
    so the caller can report what actually happened.
    """
    safe   = re.sub(r'[\\/*?:"<>|]', "", label)[:11]
    letter = drive.rstrip(":\\")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f'Set-Volume -DriveLetter {letter} -NewFileSystemLabel "{safe}"'],
        capture_output=True,
    )
    return r.returncode == 0, safe


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


def load_presets():
    if PRESETS_FILE.exists():
        with open(PRESETS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_presets(presets):
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    with open(PRESETS_FILE, "w", encoding="utf-8") as f:
        json.dump(presets, f, ensure_ascii=False, indent=2)


def safe_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip() or "video"


def unique_filename(base, used_names):
    """Disambiguate <base>.dat collisions across a batch (e.g. a hash
    prefix collision) by appending _2, _3, ... before the extension.

    Uses a bland .dat extension and a non-human-readable base (the
    caller passes a hash prefix, not the display title) so the file
    doesn't stand out to someone browsing the drive.
    """
    name = f"{base}.dat"
    n = 2
    while name in used_names:
        name = f"{base}_{n}.dat"
        n += 1
    used_names.add(name)
    return name


# ---------------------------------------------------------------------------
# Main app class — plain class, runs inside any Tk/Toplevel window
# ---------------------------------------------------------------------------

class VideoEncrypterApp:
    def __init__(self, master=None, on_back=None):
        self.root     = master
        self.on_back  = on_back
        self.root.title("Video Encrypter — מגן און קי")
        self.root.geometry("560x740")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW",
                           on_back if on_back else self.root.destroy)
        self._q        = queue.Queue()
        self._logo_img = None
        self._queue    = []   # pending (video_path, title) pairs staged for this burn

        self._load_logo()
        self._build_styles()
        self._build_ui()
        self._refresh_drives()
        self._refresh_presets_combo()
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

        # Quick burn — reload a saved event/video-list preset and burn it
        # straight away, for repeat runs of the same batch onto new keys.
        fq = ttk.Frame(self.root)
        fq.pack(fill="x", padx=24, pady=(0, 4))
        ttk.Label(fq, text=f"{_R}הגדרה שמורה", width=12, anchor="e",
                  font=("Segoe UI", 10)).pack(side="right")
        self.preset_var = tk.StringVar()
        self.preset_combo = ttk.Combobox(fq, textvariable=self.preset_var,
                                         width=20, state="readonly", justify="right")
        self.preset_combo.pack(side="right", padx=6)
        self.preset_combo.bind("<<ComboboxSelected>>",
                               lambda _: self._update_preset_buttons())
        self.load_burn_btn = ttk.Button(fq, text=f"{_R}טען וצרוב",
                                        style="Accent.TButton",
                                        command=self._quick_burn)
        self.delete_preset_btn = ttk.Button(fq, text=f"{_R}מחק הגדרה",
                                            command=self._delete_preset)
        self._preset_btn_frame = fq

        # Event name row — used for the exe filename + drive label, since a
        # key can now hold several videos with no single title of its own.
        f0 = ttk.Frame(self.root)
        f0.pack(fill="x", **P)
        ttk.Label(f0, text=f"{_R}שם האירוע", width=12, anchor="e",
                  font=("Segoe UI", 10)).pack(side="right")
        self.event_entry = self._make_rtl_text(f0)
        self.event_entry.pack(side="right", padx=6)
        ttk.Label(f0, text=f"{_R}שם הכונן + שם הקובץ להרצה",
                  style="Hint.TLabel").pack(side="left", padx=4)

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
        self.title_entry = self._make_rtl_text(f3, width=24)
        self.title_entry.pack(side="right", padx=6)
        ttk.Label(f3, text=f"{_R}מוצג ללקוח",
                  style="Hint.TLabel").pack(side="left", padx=4)
        ttk.Button(f3, text=f"{_R}+ הוסף לרשימה",
                  command=self._add_to_queue).pack(side="left", padx=4)

        # Queue of videos staged for this burn
        f4 = ttk.Frame(self.root)
        f4.pack(fill="x", padx=24, pady=(2, 4))
        ttk.Label(f4, text=f"{_R}סרטונים לצריבה:", anchor="e",
                  font=("Segoe UI", 9)).pack(side="right")
        ttk.Button(f4, text=f"{_R}הסר מהרשימה",
                  command=self._remove_from_queue).pack(side="left")

        f5 = ttk.Frame(self.root)
        f5.pack(fill="x", padx=24, pady=(0, 4))
        self.queue_list = tk.Listbox(f5, height=5, justify="right",
                                     activestyle="dotbox", exportselection=False)
        self.queue_list.pack(side="left", fill="x", expand=True)
        qscroll = ttk.Scrollbar(f5, orient="vertical", command=self.queue_list.yview)
        qscroll.pack(side="left", fill="y")
        self.queue_list.config(yscrollcommand=qscroll.set)

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

    def _make_rtl_text(self, parent, width=34):
        # tk.Text instead of ttk.Entry — fixes Hebrew RTL character ordering bug in tkinter
        s = ttk.Style()
        ebg = s.lookup("TEntry", "fieldbackground") or "#1c1c1c"
        efg = s.lookup("TEntry", "foreground") or "#ffffff"
        widget = tk.Text(
            parent, height=1, width=width, wrap="none",
            font=("Segoe UI", 10),
            bg=ebg, fg=efg, insertbackground=efg,
            relief="flat", bd=1,
            selectbackground="#264f78", selectforeground="#ffffff",
        )
        widget.bind("<Return>", lambda e: "break")
        return widget

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
        if fh in lib and lib[fh].get("key_fp") == _KEY_FP and Path(lib[fh]["enc"]).exists():
            self.lib_lbl.config(text=f"{_R}נמצא בספרייה — אין צורך בהצפנה מחדש",
                                style="Green.TLabel")
        else:
            self.lib_lbl.config(text=f"{_R}לא בספרייה — יוצפן בעת הכתיבה",
                                style="Orange.TLabel")

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def _add_to_queue(self):
        video = self.video_var.get()
        title = self.title_entry.get("1.0", "end-1c").strip()
        if not video or not Path(video).exists():
            messagebox.showerror("שגיאה", "יש לבחור קובץ וידאו תקין.", parent=self.root); return
        if not title:
            messagebox.showerror("שגיאה", "יש להזין שם להצגה עבור הסרטון.", parent=self.root); return
        self._queue.append((video, title))
        self._refresh_queue_list()
        self.video_var.set("")
        self.title_entry.delete("1.0", "end")
        self.lib_lbl.config(text="")

    def _remove_from_queue(self):
        sel = self.queue_list.curselection()
        if not sel:
            return
        del self._queue[sel[0]]
        self._refresh_queue_list()

    def _refresh_queue_list(self):
        self.queue_list.delete(0, "end")
        for video, title in self._queue:
            self.queue_list.insert("end", f"{title}  —  {Path(video).name}")
        n = len(self._queue)
        if n == 0:
            self.burn_btn.config(text=f"{_R}התחל הצפנה")
        elif n == 1:
            self.burn_btn.config(text=f"{_R}צרוב סרטון אחד לכונן")
        else:
            self.burn_btn.config(text=f"{_R}צרוב {n} סרטונים לכונן")

    # ------------------------------------------------------------------
    # Presets (quick burn)
    # ------------------------------------------------------------------

    def _refresh_presets_combo(self):
        names = sorted(load_presets().keys())
        self.preset_combo["values"] = names
        if self.preset_var.get() not in names:
            self.preset_var.set("")
        self._update_preset_buttons()

    def _update_preset_buttons(self):
        if self.preset_var.get():
            if not self.load_burn_btn.winfo_ismapped():
                self.load_burn_btn.pack(side="left", padx=4)
            if not self.delete_preset_btn.winfo_ismapped():
                self.delete_preset_btn.pack(side="left", padx=4)
        else:
            self.load_burn_btn.pack_forget()
            self.delete_preset_btn.pack_forget()

    def _delete_preset(self):
        name = self.preset_var.get()
        if not name:
            return
        if not messagebox.askyesno(
            "מחיקת הגדרה", f'{_R}למחוק את ההגדרה "{name}"?', parent=self.root,
        ):
            return
        presets = load_presets()
        presets.pop(name, None)
        save_presets(presets)
        self.preset_var.set("")
        self._refresh_presets_combo()

    def _quick_burn(self):
        name = self.preset_var.get()
        if not name:
            messagebox.showerror("שגיאה", "יש לבחור הגדרה שמורה מהרשימה.", parent=self.root); return
        preset = load_presets().get(name)
        if not preset:
            messagebox.showerror("שגיאה", "ההגדרה לא נמצאה.", parent=self.root); return
        missing = [it["video"] for it in preset["items"] if not Path(it["video"]).exists()]
        if missing:
            messagebox.showerror("שגיאה",
                "קבצי הוידאו הבאים מההגדרה לא נמצאו:\n" + "\n".join(missing),
                parent=self.root); return

        self.event_entry.delete("1.0", "end")
        self.event_entry.insert("1.0", preset["event_name"])
        self.video_var.set("")
        self.title_entry.delete("1.0", "end")
        self._queue = [(it["video"], it["title"]) for it in preset["items"]]
        self._refresh_queue_list()
        self._burn()

    def _offer_save_preset(self, event_name, items):
        if not self.root.winfo_exists():
            return
        if not messagebox.askyesno(
            "שמירת הגדרה",
            "לשמור את ההגדרה הזו (שם אירוע + רשימת סרטונים) לצריבה מהירה בפעם הבאה?",
            parent=self.root,
        ):
            return
        name = simpledialog.askstring(
            "שמירת הגדרה", "שם לשמירה:", parent=self.root,
        )
        name = (name or "").strip()
        if not name:
            return
        presets = load_presets()
        presets[name] = {
            "event_name": event_name,
            "items": [{"video": str(video), "title": title} for video, title in items],
        }
        save_presets(presets)
        self._refresh_presets_combo()

    def _pending_queue(self):
        """The staged queue, plus whatever's still sitting in the input
        fields but not yet explicitly added — keeps the single-video flow
        exactly as simple as before (fill fields, click burn, done)."""
        items = list(self._queue)
        video = self.video_var.get()
        title = self.title_entry.get("1.0", "end-1c").strip()
        if video and Path(video).exists() and title:
            items.append((video, title))
        return items

    def _burn(self):
        drive = self.drive_var.get()
        event_name = self.event_entry.get("1.0", "end-1c").strip()
        items = self._pending_queue()
        if not drive:
            messagebox.showerror("שגיאה", "יש לבחור כונן USB.", parent=self.root); return
        if not items:
            messagebox.showerror("שגיאה", "יש להוסיף לפחות סרטון אחד לרשימה.", parent=self.root); return
        if not event_name:
            messagebox.showerror("שגיאה", "יש להזין שם לאירוע.", parent=self.root); return
        if not drive_is_empty(drive):
            messagebox.showerror("שגיאה",
                f"הכונן {drive} אינו ריק.\n\n"
                "יש לפרמט אותו (לחיצה ימנית ← פרמט בסייר) ולנסות שוב.",
                parent=self.root); return
        client_dir = self._find_client()
        if not client_dir:
            messagebox.showerror("שגיאה",
                "תיקיית client (עם client.exe) לא נמצאה.\n"
                "יש להניח אותה בתיקייה שבה נמצא NitzFlash.exe.",
                parent=self.root); return

        self.burn_btn.config(state="disabled")
        threading.Thread(target=self._worker,
                         args=(drive, items, event_name, client_dir), daemon=True).start()

    def _find_client(self):
        """Locate the onedir client build folder (client.exe + vlc_runtime +
        _internal). Returns the folder, not the exe, since the whole tree
        needs to be copied to the USB.
        """
        from core.utils import resource_dir
        candidates = [
            resource_dir() / "client",
            Path(sys.executable).parent / "client",
            Path(__file__).resolve().parent.parent / "client",
            Path(__file__).resolve().parent / "dist" / "client",
        ]
        for p in candidates:
            if (p / "client.exe").exists():
                return p
        return None

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    def _worker(self, drive, items, event_name, client_dir):
        try:
            self._log(f"{_R}מתחיל תהליך כתיבה עבור {len(items)} סרטונים...")
            lib        = load_library()
            drive_root = Path(drive + "\\")
            media      = drive_root / "SystemCache"
            media.mkdir(exist_ok=True)

            sizes      = [os.path.getsize(video_path) for video_path, _ in items]
            total_size = sum(sizes) or 1
            done_size  = 0
            used_names = set()
            videos_meta = []

            # Each item's encrypt+copy work is weighted by its source file
            # size so the single progress bar sweeps smoothly across the
            # whole batch instead of resetting per video.
            for (video_path, title), size in zip(items, sizes):
                fh            = hash_file(video_path)
                original_name = Path(video_path).name
                ext           = Path(video_path).suffix
                base_done     = done_size
                # Only reuse a cached encryption if it was made under the
                # *current* access code — otherwise a rotated code would
                # silently ship stale ciphertext that the new client.exe
                # (built with the new code) could never decrypt.
                cached = (fh in lib and lib[fh].get("key_fp") == _KEY_FP
                         and Path(lib[fh]["enc"]).exists())

                if cached:
                    enc_path = Path(lib[fh]["enc"])
                    self._log(f"{_R}{title}: נמצא בספרייה — משתמש ב-{enc_path.name}")
                else:
                    ENCRYPTED_DIR.mkdir(parents=True, exist_ok=True)
                    enc_path = ENCRYPTED_DIR / f"{fh[:16]}.enc"
                    self._log(f"{_R}{title}: מצפין (עשוי לקחת זמן)...")

                    def ep(pct, base_done=base_done, size=size, title=title):
                        self._set_progress((base_done + pct * size) / total_size * 90,
                                           f"{_R}מצפין את \"{title}\"... {int(pct*100)}%")

                    encrypt_to(video_path, enc_path, ep)
                    lib[fh] = {"enc": str(enc_path), "name": original_name, "key_fp": _KEY_FP}
                    save_library(lib)
                    self._log(f"{_R}{title}: הוצפן ונשמר בספרייה.")

                enc_filename = unique_filename(fh[:16], used_names)
                dst_enc      = media / enc_filename

                def cp(pct, base_done=base_done, size=size, title=title):
                    self._set_progress((base_done + pct * size) / total_size * 90,
                                       f"{_R}מעתיק את \"{title}\"... {int(pct*100)}%")

                copy_with_progress(enc_path, dst_enc, cp)
                hide_path(dst_enc)
                videos_meta.append({"title": title, "ext": ext, "enc": enc_filename})
                done_size += size

            label_ok, applied_label = set_drive_label(drive, event_name)
            if label_ok:
                self._log(f"{_R}שם הכונן עודכן: {applied_label}")
            else:
                self._log(f"{_R}אזהרה: לא הצלחתי לשנות את שם הכונן.")

            dst_meta = media / "meta.json"
            dst_meta.write_text(
                json.dumps({"event": event_name, "videos": videos_meta},
                          ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            hide_path(dst_meta)
            hide_path(media)

            self._set_progress(92, f"{_R}מעתיק את נגן ה-USB (כולל LibVLC)...")
            exe_name     = safe_filename(event_name) + ".exe"
            support_dirs = copy_client_tree(client_dir, drive_root, exe_name)
            for d in support_dirs:
                hide_path(d)
            self._log(f"{_R}הקבצים הועתקו והוסתרו.")

            instructions_pdf = Path(_resource_path(INSTRUCTIONS_PDF_RESOURCE))
            if instructions_pdf.exists():
                shutil.copy2(instructions_pdf, drive_root / INSTRUCTIONS_PDF_NAME)
                self._log(f"{_R}קובץ ההוראות הועתק לכונן.")
            else:
                self._log(f"{_R}אזהרה: קובץ ההוראות ({INSTRUCTIONS_PDF_NAME}) לא נמצא ולא הועתק.")

            self._set_progress(100, f"{_R}הסתיים!")
            self._log(f"{_R}הכתיבה הושלמה! הכונן מוכן.")

            def _on_finished():
                messagebox.showinfo(
                    "הסתיים",
                    f"הכונן {drive} מוכן!\n\n"
                    f"הלקוח לוחץ פעמיים על:\n{exe_name}\n\n"
                    f"{len(items)} סרטונים נכתבו.",
                    parent=self.root,
                )
                self._offer_save_preset(event_name, items)

            self._ui(_on_finished)
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
