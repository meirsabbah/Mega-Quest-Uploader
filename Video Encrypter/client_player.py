"""
מגן און קי — ניצחה הרוח
נגן וידאו לכונן USB
"""

import ctypes
import json
import os
import sys
import time
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.backends import default_backend

# ── Must match setup_usb.py ───────────────────────────────────────────────────
# Deliberately, there is no ACCESS_CODE constant in this file. This module
# ships to the customer (inside client.exe, on the very USB key it's meant
# to protect), so it must not contain the code, a hash of it, or any other
# reference value a guess could be checked against. It only has the public
# KDF salt/parameters below — the actual key is derived fresh from whatever
# the customer types in, and the only way to tell a guess is right is that
# it actually decrypts a video into something that looks like a video (see
# _looks_like_video below).
_KDF_SALT = bytes.fromhex(
    "a3f8e2b1c4d5e6f708192a3b4c5d6e7f"
    "809102030405060708090a0b0c0d0e0f"
)


def derive_key(password):
    """Must match setup_usb.py's derive_key exactly (same salt/n/r/p) —
    otherwise even the correct code would derive a different key."""
    kdf = Scrypt(salt=_KDF_SALT, length=32, n=2 ** 17, r=8, p=1)
    return kdf.derive(password.encode("utf-8"))


# MP4/MOV files start with a box-size (4 bytes) then the ASCII tag "ftyp".
# Decrypting the first 8 bytes with the right key and checking for that tag
# is how a correct code is told apart from a wrong one, without ever
# storing a password verifier anywhere.
_CONTAINER_SIGNATURES = {
    ".mp4": b"ftyp", ".m4v": b"ftyp", ".mov": b"ftyp",
}


def _looks_like_video(key, enc_path, ext):
    sig = _CONTAINER_SIGNATURES.get(ext.lower())
    if sig is None:
        return True  # unrecognized container — can't verify, trust the input
    try:
        stream = EncryptedStream(enc_path, key)
        header = stream.read(8)
        stream.close()
    except Exception:
        return False
    return len(header) >= 8 and header[4:8] == sig


def _vlc_runtime_dir():
    """Bundled LibVLC folder next to the exe, if this is the built client."""
    base = (Path(sys.executable).parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent)
    candidate = base / "vlc_runtime"
    return candidate if (candidate / "libvlc.dll").exists() else None


# Must run before `import vlc`, since python-vlc resolves the library location
# at import time. When absent (e.g. running from source on a dev machine with
# VLC installed system-wide), python-vlc falls back to its normal discovery.
_VLC_DIR = _vlc_runtime_dir()
if _VLC_DIR:
    os.add_dll_directory(str(_VLC_DIR))
    os.environ["PYTHON_VLC_LIB_PATH"] = str(_VLC_DIR / "libvlc.dll")
    os.environ["PYTHON_VLC_MODULE_PATH"] = str(_VLC_DIR)

import vlc  # noqa: E402  (import position depends on the block above)

# ── Brand colours ─────────────────────────────────────────────────────────────
BG    = "#ffffff"
FG    = "#0d2054"
BLUE  = "#1d80c8"
LIGHT = "#e8f2fb"
GREY  = "#6b7280"
DARK  = "#000000"


def resource_path(rel):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, rel)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)


def find_media_list():
    """Return (event_name, videos) where videos is a list of
    {"title", "ext", "enc": Path} dicts.

    media/meta.json (new multi-video format) is tried first; if absent,
    falls back to the legacy single-video media/meta.txt + first-.enc-found
    format so USB keys burned before this feature keep working unchanged.
    """
    base  = Path(sys.executable).parent
    media = base / "SystemCache"

    json_path = media / "meta.json"
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        videos = []
        for v in data.get("videos", []):
            ext = v.get("ext", ".mp4")
            if not ext.startswith("."):
                ext = "." + ext
            enc_path = media / v.get("enc", "")
            if enc_path.exists():
                videos.append({"title": v.get("title", "וידאו"), "ext": ext,
                              "enc": enc_path})
        return data.get("event", "מגן און קי"), videos

    meta_path = media / "meta.txt"
    if not meta_path.exists():
        return None, []

    lines = meta_path.read_text(encoding="utf-8").splitlines()
    title = lines[0].strip() if len(lines) >= 1 else "וידאו"
    ext   = lines[1].strip() if len(lines) >= 2 else ".mp4"
    if not ext.startswith("."):
        ext = "." + ext

    enc_files = sorted(media.glob("*.enc"))
    if not enc_files:
        return None, []

    return None, [{"title": title, "ext": ext, "enc": enc_files[0]}]


class EncryptedStream:
    """Random-access AES-256-CTR decrypting reader over a .enc file.

    CTR mode decrypts each 16-byte block independently, so seeking just
    means recomputing the counter for the target block instead of decrypting
    everything from the start — which is what makes streaming playback of a
    still-encrypted file possible.
    """

    BLOCK = 16

    def __init__(self, enc_path, key):
        self._key = key
        self._fh = open(enc_path, "rb")
        self._nonce = self._fh.read(self.BLOCK)
        self.size = os.path.getsize(enc_path) - self.BLOCK
        self._pos = 0
        self._skip = 0
        self._decryptor = None
        self._reset(0)

    def _reset(self, pos):
        block_index = pos // self.BLOCK
        aligned = block_index * self.BLOCK
        counter = (int.from_bytes(self._nonce, "big") + block_index) % (1 << 128)
        cipher = Cipher(algorithms.AES(self._key), modes.CTR(counter.to_bytes(16, "big")),
                        backend=default_backend())
        self._decryptor = cipher.decryptor()
        self._fh.seek(self.BLOCK + aligned)
        self._skip = pos - aligned

    def seek(self, pos):
        pos = max(0, min(pos, self.size))
        if pos != self._pos:
            self._reset(pos)
            self._pos = pos

    def read(self, n):
        if self._pos >= self.size:
            return b""
        n = min(n, self.size - self._pos)
        raw = self._fh.read(n + self._skip)
        if not raw:
            return b""
        plain = self._decryptor.update(raw)
        if self._skip:
            plain = plain[self._skip:]
            self._skip = 0
        self._pos += len(plain)
        return plain

    def close(self):
        self._fh.close()


def make_vlc_callbacks(stream: EncryptedStream):
    """Wrap an EncryptedStream as the four libVLC media-input callbacks.

    The real CFUNCTYPE-based callback types live under
    vlc.CallbackDecorators — the bare vlc.MediaOpenCb etc. are just
    documentation stub classes (subclasses of c_void_p) and will raise
    "cannot be converted to pointer" if used directly as decorators.
    """
    cb = vlc.CallbackDecorators

    @cb.MediaOpenCb
    def open_cb(opaque, data_ptr, size_ptr):
        size_ptr[0] = stream.size
        return 0

    @cb.MediaReadCb
    def read_cb(opaque, buf, length):
        try:
            chunk = stream.read(length)
        except Exception:
            return -1
        if not chunk:
            return 0
        ctypes.memmove(buf, chunk, len(chunk))
        return len(chunk)

    @cb.MediaSeekCb
    def seek_cb(opaque, offset):
        try:
            stream.seek(offset)
        except Exception:
            return -1
        return 0

    @cb.MediaCloseCb
    def close_cb(opaque):
        stream.close()

    # Keep references alive for as long as the media/player exist — ctypes
    # does not keep callback wrapper objects alive on its own.
    return open_cb, read_cb, seek_cb, close_cb


def _fmt_ms(ms):
    if ms is None or ms < 0:
        return "00:00"
    total = ms // 1000
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class PlayerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("מגן און קי")
        self.geometry("400x310")
        self.resizable(False, False)
        self.configure(bg=BG)
        self._logo_img   = None
        self._icon_img   = None
        self._stream     = None
        self._instance   = None
        self._media      = None
        self._player     = None
        self._callbacks  = None
        self._seeking    = False
        self._fullscreen = False
        self._controls_visible = True
        self._last_activity    = time.time()
        self._last_pointer     = (None, None)
        self._last_volume      = 80
        self._videos     = []
        self._event_name = None
        self._key        = None

        self._load_logo()
        self._build_styles()
        self._build_splash()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(300, self._build_code_gate)

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
        s.configure("Error.TLabel", background=BG, foreground="#c0392b",
                    font=("Segoe UI", 9, "bold"))
        s.configure("TButton",      background=BLUE, foreground="white",
                    padding=(14, 6), font=("Segoe UI", 10, "bold"))
        s.map("TButton", background=[("active", "#1565a8")])
        s.configure("Ctrl.TFrame", background=DARK)
        s.configure("Ctrl.TLabel", background=DARK, foreground="#e5e7eb",
                    font=("Segoe UI", 9))
        s.configure("Ctrl.TButton", background="#1f2937", foreground="white",
                    padding=(10, 4), font=("Segoe UI", 11, "bold"))
        s.map("Ctrl.TButton", background=[("active", "#374151")])
        s.configure("Ctrl.Horizontal.TScale", background=DARK)

    # ── Splash UI ─────────────────────────────────────────────────────────────

    def _build_splash(self):
        if self._logo_img:
            tk.Label(self, image=self._logo_img, bg=BG).pack(pady=(20, 6))
        else:
            ttk.Label(self, text="מגן און קי", style="Title.TLabel").pack(pady=(32, 6))

        self.title_lbl = ttk.Label(self, text="", style="Title.TLabel")
        self.title_lbl.pack(pady=(0, 2))

        self.status_lbl = ttk.Label(self, text="אנא המתן...", style="Sub.TLabel")
        self.status_lbl.pack()

    def _set_status(self, video_title="", sub=""):
        if video_title:
            self.title_lbl.config(text=video_title)
        self.status_lbl.config(text=sub)

    # ── Access code gate ─────────────────────────────────────────────────────
    # Shown before anything else — playback can't proceed without the code
    # the organizer gave the customer separately from the USB key itself.

    def _build_code_gate(self):
        # Media is located (but not decrypted) before the prompt even shows
        # — no point asking for a code if there's nothing on the drive to
        # unlock. _check_code() below verifies a guess against this list.
        self._event_name, self._videos = find_media_list()
        if not self._videos:
            messagebox.showerror(
                "לא נמצא סרטון",
                "קובץ הוידאו לא נמצא בכונן זה.\nאנא פנה למארגן האירוע."
            )
            self.destroy()
            return

        for w in self.winfo_children():
            w.destroy()

        if self._logo_img:
            tk.Label(self, image=self._logo_img, bg=BG).pack(pady=(28, 10))
        ttk.Label(self, text="הזן קוד לצפייה", style="Title.TLabel").pack(pady=(0, 12))

        self.code_var = tk.StringVar()
        entry = ttk.Entry(self, textvariable=self.code_var, font=("Segoe UI", 14),
                          justify="center", width=14)
        entry.pack(pady=(0, 10))
        entry.focus_set()
        entry.bind("<Return>", lambda e: self._check_code())

        self.code_err_lbl = ttk.Label(self, text="", style="Error.TLabel")
        self.code_err_lbl.pack(pady=(0, 4))

        ttk.Button(self, text="פתח", command=self._check_code).pack()

    def _check_code(self):
        entered = self.code_var.get().strip()
        if not entered:
            return
        # scrypt takes a couple hundred ms by design (see derive_key) — flush
        # this label now so the click doesn't look like it did nothing.
        self.code_err_lbl.config(text="בודק...")
        self.update_idletasks()

        key = derive_key(entered)
        first = self._videos[0]
        if not _looks_like_video(key, first["enc"], first["ext"]):
            self.code_err_lbl.config(text="קוד שגוי — נסה שוב")
            self.code_var.set("")
            return

        self._key = key
        # _start()'s single-video path drives title_lbl/status_lbl, which
        # the code gate destroyed along with the rest of the splash screen
        # — rebuild it before handing off.
        self._build_splash()
        self._start()

    # ── Startup ───────────────────────────────────────────────────────────────

    def _start(self):
        # self._event_name / self._videos were already fetched by
        # _build_code_gate() before the code prompt was even shown.

        # Started once here (not per-video) so switching videos via the menu
        # never stacks a second concurrent polling loop.
        self.after(200, self._poll_player)
        self.after(500, self._check_idle)

        if len(self._videos) == 1:
            # Splash widgets (title_lbl/status_lbl) are still on screen only
            # for this very first, single-video case.
            self._set_status(self._videos[0]["title"], "פותח נגן...")
            self._play_selected(self._videos[0])
        else:
            self._build_menu_ui()

    def _play_selected(self, video):
        try:
            self._open_and_play(video["enc"], video["title"])
        except Exception as e:
            messagebox.showerror("שגיאת ניגון", str(e))
            self._cleanup_and_exit()

    def _open_and_play(self, enc_path, title):
        self._stream = EncryptedStream(enc_path, self._key)
        self._callbacks = make_vlc_callbacks(self._stream)
        self._instance = vlc.Instance("--quiet")
        self._media = self._instance.media_new_callbacks(*self._callbacks, None)
        self._player = self._instance.media_player_new()
        self._player.set_media(self._media)

        self._build_player_ui(title)
        self._bind_shortcuts()
        self.update_idletasks()
        self._player.set_hwnd(self.video_frame.winfo_id())
        self._player.audio_set_volume(int(self._last_volume))
        self._player.play()

    # ── Selection menu (shown when the key holds more than one video) ───────

    def _build_menu_ui(self):
        for w in self.winfo_children():
            w.destroy()
        self.geometry("640x520")
        self.minsize(480, 360)
        self.resizable(True, True)
        self.title(f"מגן און קי — {self._event_name}" if self._event_name else "מגן און קי")

        if self._logo_img:
            tk.Label(self, image=self._logo_img, bg=BG).pack(pady=(20, 6))
        ttk.Label(self, text="בחר סרטון לצפייה", style="Title.TLabel").pack(pady=(0, 12))

        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        vscroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        vscroll.pack(side="left", fill="y")

        rows = ttk.Frame(canvas)
        rows_id = canvas.create_window((0, 0), window=rows, anchor="nw")
        rows.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(rows_id, width=e.width))

        for video in self._videos:
            ttk.Button(rows, text=video["title"], style="TButton",
                      command=lambda v=video: self._play_selected(v)).pack(
                fill="x", pady=4, padx=4)

    def _back_to_menu(self):
        self._stop_player()
        self._player     = None
        self._stream     = None
        self._media      = None
        self._instance   = None
        self._callbacks  = None
        self._fullscreen = False
        self.attributes("-fullscreen", False)
        self._controls_visible = True
        self.config(cursor="")
        self._build_menu_ui()

    # ── Player UI ─────────────────────────────────────────────────────────────

    def _build_player_ui(self, title):
        for w in self.winfo_children():
            w.destroy()
        self.geometry("960x560")
        self.minsize(480, 320)
        self.resizable(True, True)
        self.title(f"מגן און קי — {title}")

        self.video_frame = tk.Frame(self, bg=DARK)
        self.video_frame.pack(fill="both", expand=True)
        self.video_frame.bind("<Double-Button-1>", lambda e: self._toggle_fullscreen())

        self.ctrl = ttk.Frame(self, style="Ctrl.TFrame")
        self.ctrl.pack(fill="x", side="bottom")

        self.play_btn = ttk.Button(self.ctrl, text="⏸", width=3, style="Ctrl.TButton",
                                   takefocus=0, command=self._toggle_play)
        self.play_btn.pack(side="left", padx=(10, 6), pady=8)

        self.time_lbl = ttk.Label(self.ctrl, text="00:00", style="Ctrl.TLabel", width=8)
        self.time_lbl.pack(side="left")

        self.seek_var = tk.DoubleVar(value=0)
        self.seek_scale = ttk.Scale(self.ctrl, from_=0, to=1000, orient="horizontal",
                                    variable=self.seek_var, takefocus=0,
                                    style="Ctrl.Horizontal.TScale")
        self.seek_scale.pack(side="left", fill="x", expand=True, padx=8)
        self._bind_clickable_scale(self.seek_scale, self._on_seek_drag, self._on_seek_release)

        self.dur_lbl = ttk.Label(self.ctrl, text="00:00", style="Ctrl.TLabel", width=8)
        self.dur_lbl.pack(side="left", padx=(0, 6))

        self.mute_btn = ttk.Button(self.ctrl, text="🔊", width=3, style="Ctrl.TButton",
                                   takefocus=0, command=self._toggle_mute)
        self.mute_btn.pack(side="left", padx=(4, 2), pady=8)

        self.volume_var = tk.DoubleVar(value=self._last_volume)
        self.volume_scale = ttk.Scale(self.ctrl, from_=0, to=100, orient="horizontal",
                                      variable=self.volume_var, takefocus=0,
                                      length=90, style="Ctrl.Horizontal.TScale")
        self.volume_scale.pack(side="left", padx=(0, 8))
        self._bind_clickable_scale(self.volume_scale, self._on_volume_drag, None)

        ttk.Button(self.ctrl, text="⛶", width=3, style="Ctrl.TButton", takefocus=0,
                  command=self._toggle_fullscreen).pack(side="left", padx=4, pady=8)

        # With only one video on the key, closing quits the app — same as
        # before this feature existed. With several, it returns to the menu
        # instead so guests can pick another one without relaunching.
        if len(self._videos) > 1:
            ttk.Button(self.ctrl, text="חזרה לתפריט", style="Ctrl.TButton", takefocus=0,
                      command=self._back_to_menu).pack(side="left", padx=(4, 10), pady=8)
        else:
            ttk.Button(self.ctrl, text="סיום — סגור", style="Ctrl.TButton", takefocus=0,
                      command=self._on_close).pack(side="left", padx=(4, 10), pady=8)

    # ── Clickable seek/volume scales ─────────────────────────────────────────
    # ttk.Scale's default trough-click only nudges by one step towards the
    # click point instead of jumping there. Overriding Button-1/B1-Motion and
    # returning "break" gives click-to-position behavior like a normal
    # video-player scrubber.

    def _bind_clickable_scale(self, scale, on_change, on_release):
        def set_from_event(event):
            self._on_activity()
            width = scale.winfo_width()
            frac = min(1.0, max(0.0, event.x / max(width, 1)))
            lo, hi = scale["from"], scale["to"]
            on_change(float(lo) + frac * (float(hi) - float(lo)))
            return "break"

        scale.bind("<Button-1>", set_from_event)
        scale.bind("<B1-Motion>", set_from_event)
        if on_release:
            scale.bind("<ButtonRelease-1>", lambda e: on_release())

    def _on_seek_drag(self, value):
        self._seeking = True
        self.seek_var.set(value)
        length = self._player.get_length()
        if length and length > 0:
            self._player.set_time(int(value / 1000 * length))

    def _on_seek_release(self):
        self._seeking = False

    def _on_volume_drag(self, value):
        self.volume_var.set(value)
        self._set_volume(value)

    def _set_volume(self, value):
        value = max(0, min(100, value))
        self._player.audio_set_volume(int(value))
        if value > 0:
            self._last_volume = value
        self.mute_btn.config(text="🔇" if value <= 0 else "🔊")

    def _toggle_mute(self):
        if self.volume_var.get() > 0:
            self._last_volume = self.volume_var.get()
            self.volume_var.set(0)
            self._set_volume(0)
        else:
            restored = self._last_volume or 80
            self.volume_var.set(restored)
            self._set_volume(restored)

    def _toggle_play(self):
        # set_pause(1/0) explicitly, rather than the toggle pause() command —
        # pause() is unreliable against this callback-fed media source.
        if not self._player:
            return
        if self._player.is_playing():
            self._player.set_pause(1)
        else:
            self._player.set_pause(0)

    def _toggle_fullscreen(self):
        self._fullscreen = not self._fullscreen
        self.attributes("-fullscreen", self._fullscreen)
        self._on_activity()

    # ── Keyboard shortcuts ───────────────────────────────────────────────────

    def _bind_shortcuts(self):
        self.bind_all("<space>", self._on_space)
        self.bind_all("<Escape>", self._on_escape)
        self.bind_all("<KeyPress-f>", lambda e: self._toggle_fullscreen())
        self.bind_all("<KeyPress-F>", lambda e: self._toggle_fullscreen())
        self.bind_all("<Left>", lambda e: self._seek_relative(-10000))
        self.bind_all("<Right>", lambda e: self._seek_relative(10000))
        self.bind_all("<Up>", lambda e: self._volume_relative(5))
        self.bind_all("<Down>", lambda e: self._volume_relative(-5))
        self.bind_all("<Motion>", lambda e: self._on_activity())
        self.bind_all("<Button>", lambda e: self._on_activity())

    def _on_space(self, _event):
        self._toggle_play()
        self._on_activity()
        return "break"

    def _on_escape(self, _event):
        if self._fullscreen:
            self._toggle_fullscreen()

    def _seek_relative(self, delta_ms):
        if not self._player:
            return
        length = self._player.get_length()
        ms = self._player.get_time()
        if length and length > 0:
            self._player.set_time(max(0, min(length, ms + delta_ms)))
            self._on_activity()

    def _volume_relative(self, delta):
        if not self._player:
            return
        value = max(0, min(100, self.volume_var.get() + delta))
        self.volume_var.set(value)
        self._set_volume(value)
        self._on_activity()

    # ── Auto-hide controls (fullscreen presentation shouldn't show a UI) ────

    def _on_activity(self):
        self._last_activity = time.time()
        if not self._controls_visible:
            self._controls_visible = True
            self.ctrl.pack(fill="x", side="bottom")
            self.config(cursor="")

    def _check_idle(self):
        # bind_all("<Motion>") only sees mouse movement over real Tk widgets.
        # The embedded VLC video surface is a separate native child window
        # (set_hwnd), so it intercepts mouse-move at the OS level before Tk
        # ever hears about it — polling the actual cursor position instead
        # catches movement anywhere, including over the video itself.
        pointer = (self.winfo_pointerx(), self.winfo_pointery())
        if pointer != self._last_pointer:
            self._last_pointer = pointer
            self._on_activity()
        elif (self._controls_visible and self._player and self._player.is_playing()
                and time.time() - self._last_activity > 2.5):
            self._controls_visible = False
            self.ctrl.pack_forget()
            self.config(cursor="none")
        self.after(200, self._check_idle)

    def _poll_player(self):
        # Guarded rather than an early-return, so this keeps rescheduling
        # itself (and stays the single, ever-running instance of the loop)
        # even while self._player is None, e.g. while the menu is showing.
        if self._player:
            try:
                playing = self._player.is_playing()
                self.play_btn.config(text="⏸" if playing else "▶")
                length = self._player.get_length()
                ms     = self._player.get_time()
                if length and length > 0:
                    self.dur_lbl.config(text=_fmt_ms(length))
                    if not self._seeking:
                        self.seek_var.set(max(0, min(1000, ms / length * 1000)))
                self.time_lbl.config(text=_fmt_ms(ms))
            except Exception:
                pass
        self.after(300, self._poll_player)

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def _cleanup_and_exit(self):
        self._stop_player()
        self.destroy()

    def _stop_player(self):
        if self._player:
            try:
                self._player.stop()
            except Exception:
                pass
        if self._stream:
            try:
                self._stream.close()
            except Exception:
                pass

    def _on_close(self):
        self._stop_player()
        self.destroy()


if __name__ == "__main__":
    app = PlayerApp()
    app.mainloop()
