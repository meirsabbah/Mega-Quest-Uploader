"""
מגן און קי — ניצחה הרוח
הצפנת וידאו וכתיבה לכונן USB
"""

import os, sys, json, hashlib, shutil, subprocess, threading, queue, re
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

# ── Shared encryption key (must match client_player.py) ──────────────────────
ENCRYPTION_KEY = bytes.fromhex(
    "a3f8e2b1c4d5e6f708192a3b4c5d6e7f"
    "809102030405060708090a0b0c0d0e0f"
)

LIBRARY_DIR   = Path.home() / "VideoEncrypterLibrary"
ENCRYPTED_DIR = LIBRARY_DIR / "encrypted"
LIBRARY_INDEX = LIBRARY_DIR / "library.json"
CHUNK         = 8 * 1024 * 1024   # 8 MB

SYSTEM_NAMES = {"System Volume Information", "$RECYCLE.BIN", "desktop.ini",
                "RECYCLER", "Thumbs.db", "autorun.inf"}

# ── Brand colours ─────────────────────────────────────────────────────────────
BG        = "#111827"
HEADER_BG = "#ffffff"
FG        = "#e5e7eb"
FG_DARK   = "#0d2054"
GREEN     = "#22c55e"
RED       = "#ef4444"
ORANGE    = "#f97316"
BLUE      = "#1d80c8"
SURFACE   = "#1f2937"
DARK      = "#0a0f1a"


def resource_path(rel):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, rel)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_removable_drives():
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_LogicalDisk | "
             "Where-Object {$_.DriveType -eq 2} | "
             "Select-Object -ExpandProperty DeviceID"],
            capture_output=True, text=True, timeout=10
        )
        drives = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if len(line) == 2 and line[1] == ":":
                drives.append(line)
        return drives
    except Exception:
        return []


def drive_is_empty(drive):
    root = Path(drive + "\\")
    try:
        return all(p.name in SYSTEM_NAMES for p in root.iterdir())
    except Exception:
        return False


def hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def encrypt_to(src, dst, on_progress=None):
    nonce  = os.urandom(16)
    cipher = Cipher(algorithms.AES(ENCRYPTION_KEY), modes.CTR(nonce),
                    backend=default_backend())
    enc    = cipher.encryptor()
    total  = os.path.getsize(src)
    done   = 0
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
    """Rename the USB volume label as it appears in Windows Explorer."""
    safe  = re.sub(r'[\\/*?:"<>|]', '', label)[:32]
    letter = drive.rstrip(':\\')
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f'Set-Volume -DriveLetter {letter} -NewFileSystemLabel "{safe}"'],
        capture_output=True
    )


def load_library():
    if LIBRARY_INDEX.exists():
        with open(LIBRARY_INDEX) as f:
            return json.load(f)
    return {}


def safe_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip() or "video"


def save_library(lib):
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    ENCRYPTED_DIR.mkdir(parents=True, exist_ok=True)
    with open(LIBRARY_INDEX, "w") as f:
        json.dump(lib, f, indent=2)


# ── Main application ──────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("מגן און קי")
        self.geometry("560x540")
        self.resizable(False, False)
        self.configure(bg=BG)
        self._q        = queue.Queue()
        self._logo_img = None
        self._icon_img = None

        self._load_logo()
        self._build_styles()
        self._build_ui()
        self._refresh_drives()
        self.after(80, self._pump_queue)

    def _load_logo(self):
        try:
            img = Image.open(resource_path("logo.png")).convert("RGBA")
            img.thumbnail((64, 64), Image.LANCZOS)
            self._logo_img = ImageTk.PhotoImage(img)
            icon = Image.open(resource_path("logo.png")).convert("RGBA")
            icon.thumbnail((32, 32), Image.LANCZOS)
            self._icon_img = ImageTk.PhotoImage(icon)
            self.iconphoto(True, self._icon_img)
        except Exception:
            pass

    # ── Thread-safe UI bridge ─────────────────────────────────────────────────

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
        s.configure("TFrame",         background=BG)
        s.configure("Header.TFrame",  background=HEADER_BG)
        s.configure("TLabel",         background=BG,        foreground=FG,
                    font=("Segoe UI", 10))
        s.configure("Header.TLabel",  background=HEADER_BG, foreground=FG_DARK,
                    font=("Segoe UI", 14, "bold"))
        s.configure("Sub.TLabel",     background=HEADER_BG, foreground="#5b7ba8",
                    font=("Segoe UI", 9))
        s.configure("Hint.TLabel",    background=BG,        foreground="#6b7280",
                    font=("Segoe UI", 9))
        s.configure("Green.TLabel",   background=BG,        foreground=GREEN)
        s.configure("Red.TLabel",     background=BG,        foreground=RED)
        s.configure("Orange.TLabel",  background=BG,        foreground=ORANGE)
        s.configure("TButton",        background=SURFACE,   foreground=FG,      padding=(8, 4))
        s.map("TButton", background=[("active", "#374151")])
        s.configure("Burn.TButton",   background=BLUE,      foreground="white",
                    padding=(14, 6),  font=("Segoe UI", 10, "bold"))
        s.map("Burn.TButton", background=[("active", "#1565a8"), ("disabled", SURFACE)])
        s.configure("TCombobox",      fieldbackground=SURFACE, foreground=FG,
                    selectbackground=SURFACE, selectforeground=FG)
        s.configure("TEntry",         fieldbackground=SURFACE, foreground=FG,
                    insertcolor=FG)
        s.configure("TProgressbar",   troughcolor=SURFACE,  background=BLUE, thickness=14)

    # ── UI layout (RTL) ───────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Branded white header ──────────────────────────────────────────────
        header = ttk.Frame(self, style="Header.TFrame", height=80)
        header.pack(fill="x")
        header.pack_propagate(False)

        # Logo on the right side for RTL
        if self._logo_img:
            tk.Label(header, image=self._logo_img, bg=HEADER_BG).pack(
                side="right", padx=(10, 16), pady=8)

        title_frame = ttk.Frame(header, style="Header.TFrame")
        title_frame.pack(side="right", pady=8)
        ttk.Label(title_frame, text="מגן און קי",              style="Header.TLabel").pack(anchor="e")
        ttk.Label(title_frame, text="הצפנת וידאו לכונן",  style="Sub.TLabel").pack(anchor="e")

        # ── Divider ───────────────────────────────────────────────────────────
        tk.Frame(self, bg=BLUE, height=3).pack(fill="x")

        P = dict(padx=24, pady=5)

        # ── Drive row ─────────────────────────────────────────────────────────
        f1 = ttk.Frame(self); f1.pack(fill="x", padx=24, pady=(14, 2))
        ttk.Label(f1, text=":כונן", width=12, anchor="e").pack(side="right")
        self.drive_var   = tk.StringVar()
        self.drive_combo = ttk.Combobox(f1, textvariable=self.drive_var,
                                        width=9, state="readonly")
        self.drive_combo.pack(side="right", padx=6)
        self.drive_combo.bind("<<ComboboxSelected>>", lambda _: self._check_drive())
        self.drive_lbl = ttk.Label(f1, text="", style="Orange.TLabel")
        self.drive_lbl.pack(side="left", padx=8)

        # ── Drive info (shown only when drive needs formatting) ───────────────
        self.drive_info_lbl = ttk.Label(self, text="", style="Red.TLabel",
                                        anchor="e", wraplength=480)
        self.drive_info_lbl.pack(fill="x", padx=24, pady=(0, 4))

        # ── Video row ─────────────────────────────────────────────────────────
        # RTL visual: [עיון] [שדה] [:קובץ וידאו]
        f2 = ttk.Frame(self); f2.pack(fill="x", **P)
        ttk.Label(f2, text=":קובץ וידאו", width=12, anchor="e").pack(side="right")
        self.video_var = tk.StringVar()
        ttk.Entry(f2, textvariable=self.video_var, width=34,
                  justify="right").pack(side="right", padx=6)
        ttk.Button(f2, text="עיון", command=self._browse).pack(side="right")

        # ── Display name row ──────────────────────────────────────────────────
        # RTL visual: [(מוצג ללקוח)] [שדה] [:שם להצגה]
        f3 = ttk.Frame(self); f3.pack(fill="x", **P)
        ttk.Label(f3, text=":שם להצגה", width=12, anchor="e").pack(side="right")
        self.title_var = tk.StringVar()
        ttk.Entry(f3, textvariable=self.title_var, width=34,
                  justify="right").pack(side="right", padx=6)
        ttk.Label(f3, text="(מוצג ללקוח)", style="Hint.TLabel").pack(side="left", padx=4)

        # ── Library status ────────────────────────────────────────────────────
        self.lib_lbl = ttk.Label(self, text="", style="Orange.TLabel", anchor="e")
        self.lib_lbl.pack(fill="x", padx=24, pady=2)

        # ── Progress ──────────────────────────────────────────────────────────
        self.prog_lbl = ttk.Label(self, text="", anchor="e")
        self.prog_lbl.pack(fill="x", padx=24, pady=(10, 3))
        self.progress = ttk.Progressbar(self, length=510, mode="determinate")
        self.progress.pack(padx=24)

        # ── Burn button ───────────────────────────────────────────────────────
        self.burn_btn = ttk.Button(self, text="התחל הצפנה",
                                   style="Burn.TButton", command=self._burn)
        self.burn_btn.pack(pady=14)

        # ── Log console ───────────────────────────────────────────────────────
        self.log = tk.Text(self, height=7, bg=DARK, fg="#4ade80",
                           font=("Consolas", 10), state="disabled",
                           relief="flat", bd=0)
        self.log.tag_configure("rtl", justify="right")
        self.log.pack(fill="x", padx=24, pady=(0, 18))

    # ── Actions ───────────────────────────────────────────────────────────────

    def _log(self, msg):
        def _do():
            self.log.configure(state="normal")
            self.log.insert("end", msg + "\n", "rtl")
            self.log.see("end")
            self.log.configure(state="disabled")
        self._ui(_do)

    def _set_progress(self, value, label=""):
        def _do():
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
            self.drive_lbl.config(text="לא נמצא כונן", style="Red.TLabel")
            self.drive_info_lbl.config(text="")

    def _check_drive(self):
        d = self.drive_var.get()
        if not d:
            return
        if drive_is_empty(d):
            self.drive_lbl.config(text="מוכן (ריק)", style="Green.TLabel")
            self.drive_info_lbl.config(text="")
        else:
            self.drive_lbl.config(text="לא ריק", style="Red.TLabel")
            self.drive_info_lbl.config(
                text="הכונן אינו ריק. סגור את האפליקציה, פרמט את הכונן, ופתח מחדש."
            )

    def _browse(self):
        path = filedialog.askopenfilename(
            title="בחר קובץ וידאו",
            filetypes=[("קבצי וידאו", "*.mp4 *.mkv *.avi *.mov *.wmv *.m4v *.ts *.flv"),
                       ("כל הקבצים", "*.*")]
        )
        if not path:
            return
        self.video_var.set(path)
        if not self.title_var.get().strip():
            self.title_var.set(Path(path).stem)
        lib = load_library()
        fh  = hash_file(path)
        if fh in lib and Path(lib[fh]["enc"]).exists():
            self.lib_lbl.config(text="נמצא בספרייה — אין צורך בהצפנה מחדש",
                                style="Green.TLabel")
        else:
            self.lib_lbl.config(text="לא בספרייה — יוצפן בעת הכתיבה",
                                style="Orange.TLabel")

    def _burn(self):
        drive = self.drive_var.get()
        video = self.video_var.get()
        title = self.title_var.get().strip()
        if not drive:
            messagebox.showerror("שגיאה", "יש לבחור כונן USB."); return
        if not video or not Path(video).exists():
            messagebox.showerror("שגיאה", "יש לבחור קובץ וידאו תקין."); return
        if not title:
            messagebox.showerror("שגיאה", "יש להזין שם להצגה עבור הסרטון."); return
        if not drive_is_empty(drive):
            messagebox.showerror("שגיאה",
                f"הכונן {drive} אינו ריק.\n\n"
                "יש לפרמט אותו (לחיצה ימנית ← פרמט בסייר) ולנסות שוב."
            ); return
        client_exe = self._find_client()
        if not client_exe:
            messagebox.showerror("שגיאה",
                "הקובץ client.exe לא נמצא.\n"
                "יש להריץ את build.bat תחילה."
            ); return

        self.burn_btn.config(state="disabled")
        threading.Thread(target=self._worker,
                         args=(drive, video, title, client_exe), daemon=True).start()

    def _find_client(self):
        candidates = [
            Path(sys.executable).parent / "client.exe",
            Path(__file__).resolve().parent / "client.exe",
            Path(__file__).resolve().parent / "dist" / "client.exe",
        ]
        for p in candidates:
            if p.exists():
                return p
        return None

    # ── Background worker ─────────────────────────────────────────────────────

    def _worker(self, drive, video_path, title, client_exe):
        try:
            self._log("מתחיל תהליך כתיבה...")
            lib           = load_library()
            fh            = hash_file(video_path)
            original_name = Path(video_path).name
            ext           = Path(video_path).suffix

            # ── Step 1: get or create encrypted file ──────────────────────────
            cached = fh in lib and Path(lib[fh]["enc"]).exists()
            if cached:
                enc_path = Path(lib[fh]["enc"])
                self._log(f"נמצא בספרייה — משתמש ב-{enc_path.name}")
            else:
                ENCRYPTED_DIR.mkdir(parents=True, exist_ok=True)
                enc_path = ENCRYPTED_DIR / f"{fh[:16]}.enc"
                self._log("מצפין וידאו (עשוי לקחת זמן)...")

                def ep(pct):
                    self._set_progress(pct * 65, f"מצפין... {int(pct*100)}%")

                encrypt_to(video_path, enc_path, ep)
                lib[fh] = {"enc": str(enc_path), "name": original_name}
                save_library(lib)
                self._log(f"הוצפן ונשמר בספרייה: {enc_path.name}")

            # ── Step 2: label + copy to USB ──────────────────────────────────
            set_drive_label(drive, title)
            self._log(f"שם הכונן עודכן: {title}")
            self._log("מעתיק קבצים לכונן...")
            media        = Path(drive + "\\") / "media"
            media.mkdir(exist_ok=True)
            enc_filename = safe_filename(title) + ".enc"
            dst_enc      = media / enc_filename
            dst_meta     = media / "meta.txt"
            exe_name     = safe_filename(title) + ".exe"
            dst_client   = Path(drive + "\\") / exe_name

            # If encrypted fresh: copy fills 65-95%. If cached: copy fills 0-95%.
            copy_start = 65 if not cached else 0

            def cp(pct):
                self._set_progress(copy_start + pct * (95 - copy_start),
                                   f"מעתיק לכונן... {int(pct*100)}%")

            copy_with_progress(enc_path, dst_enc, cp)
            dst_meta.write_text(f"{title}\n{ext}", encoding="utf-8")
            shutil.copy2(client_exe, dst_client)
            self._log("הקבצים הועתקו.")

            # ── Step 3: hide media folder ─────────────────────────────────────
            hide_path(dst_enc)
            hide_path(dst_meta)
            hide_path(media)
            self._log("תיקיית המדיה הוסתרה.")

            self._set_progress(100, "!הסתיים")
            self._log("הכתיבה הושלמה! הכונן מוכן.")
            self._ui(lambda: messagebox.showinfo(
                "הסתיים",
                f"!הכונן {drive} מוכן\n\n"
                f":הלקוח לוחץ פעמיים על\n{exe_name}\n\n"
                f":קובץ מוצפן\n{enc_filename}"
            ))

        except Exception as e:
            self._log(f"שגיאה: {e}")
            self._ui(lambda: messagebox.showerror("שגיאה", str(e)))
        finally:
            self._ui(lambda: self.burn_btn.config(state="normal"))


if __name__ == "__main__":
    app = App()
    app.mainloop()
