"""All ADB / subprocess operations — no tkinter imports."""

import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def human_size(b):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


# ---------------------------------------------------------------------------
# ADB discovery
# ---------------------------------------------------------------------------

def find_adb(base_dir):
    username = os.environ.get("USERNAME", os.environ.get("USER", ""))
    candidates = [
        str(Path(base_dir) / "ADB" / "adb.exe"),
        "adb",
        rf"C:\Users\{username}\AppData\Local\Android\Sdk\platform-tools\adb.exe",
        r"C:\Program Files\Android\android-sdk\platform-tools\adb.exe",
        r"C:\Program Files (x86)\Android\android-sdk\platform-tools\adb.exe",
    ]
    for c in candidates:
        try:
            r = subprocess.run([c, "version"], capture_output=True, timeout=5,
                               creationflags=_NO_WINDOW)
            if r.returncode == 0:
                return c
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


# ---------------------------------------------------------------------------
# Device info
# ---------------------------------------------------------------------------

def get_showtime_name(adb_path, serial):
    """Read the Showtime VR device name from config.txt. Returns None if unavailable."""
    try:
        r = subprocess.run(
            [adb_path, "-s", serial, "shell", 'cat "/sdcard/Showtime VR/config.txt"'],
            capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
        )
        for line in r.stdout.strip().splitlines():
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip().lower() == "name":
                val = val.strip().strip('"').strip("'")
                if val:
                    return val
    except Exception:
        pass
    return None


def get_usb_devices_raw(adb_path):
    """Return {serial: state} for all USB-connected devices."""
    r = subprocess.run([adb_path, "devices"], capture_output=True, text=True,
                       timeout=10, creationflags=_NO_WINDOW)
    result = {}
    for line in r.stdout.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and ":" not in parts[0]:
            result[parts[0]] = parts[1]
    return result


def get_device_info_usb(adb_path, serial):
    """Return (model, display_name) for a USB-connected device."""
    try:
        r = subprocess.run(
            [adb_path, "-s", serial, "shell", "getprop ro.product.model"],
            capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
        )
        model = r.stdout.strip() or "Unknown"
        name = get_showtime_name(adb_path, serial) or model
        return model, name
    except Exception:
        return "Unknown", "Unknown"


# ---------------------------------------------------------------------------
# USB → WiFi ADB
# ---------------------------------------------------------------------------

def enable_wifi_adb(adb_path, serial):
    """Returns (success: bool, message: str)."""
    try:
        r = subprocess.run(
            [adb_path, "-s", serial, "tcpip", "5555"],
            capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW,
        )
        if r.returncode == 0:
            return True, "WiFi ADB enabled — safe to unplug"
        return False, f"Failed: {(r.stderr or r.stdout).strip()}"
    except Exception as e:
        return False, f"Error: {e}"


# ---------------------------------------------------------------------------
# Network scan
# ---------------------------------------------------------------------------

def get_local_subnet():
    import ipaddress
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
    finally:
        s.close()
    # Read the real subnet mask from ipconfig so a /23 (or other non-/24)
    # network is scanned correctly instead of being silently truncated.
    try:
        r = subprocess.run(["ipconfig"], capture_output=True, text=True,
                           timeout=5, creationflags=_NO_WINDOW)
        lines = r.stdout.splitlines()
        for i, line in enumerate(lines):
            if local_ip in line:
                for nearby in lines[max(0, i - 3):i + 4]:
                    if "255." in nearby and ":" in nearby:
                        mask = nearby.split(":")[-1].strip()
                        try:
                            net = ipaddress.IPv4Network(f"{local_ip}/{mask}", strict=False)
                            return str(net)
                        except Exception:
                            pass
    except Exception:
        pass
    return local_ip.rsplit(".", 1)[0] + ".0/24"


def probe_device(adb_path, ip_str):
    """Try to connect to ip_str:5555. Returns (model, name) or None."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        open_ = sock.connect_ex((ip_str, 5555)) == 0
        sock.close()
        if not open_:
            return None
        r = subprocess.run(
            [adb_path, "connect", f"{ip_str}:5555"],
            capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
        )
        if "connected" not in r.stdout.lower():
            return None
        model_r = subprocess.run(
            [adb_path, "-s", f"{ip_str}:5555", "shell", "getprop ro.product.model"],
            capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
        )
        model = model_r.stdout.strip() or "Unknown"
        name = get_showtime_name(adb_path, f"{ip_str}:5555") or model
        return model, name
    except Exception:
        return None


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------

def file_exists_on_device(adb_path, ip, remote_file, local_size):
    try:
        r = subprocess.run(
            [adb_path, "-s", f"{ip}:5555", "shell",
             f'stat -c%s "{remote_file}" 2>/dev/null || echo NOT_FOUND'],
            capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW,
        )
        out = r.stdout.strip()
        return out != "NOT_FOUND" and int(out) == local_size
    except Exception:
        return False


def push_file(adb_path, ip, file_path, dest_dir, progress_cb):
    """
    Push a single file. Calls progress_cb(pct) with 0-100 during transfer.
    Progress is tracked by polling the remote file size every 3 seconds
    (ADB suppresses its terminal progress output when stdout is piped).
    Returns (success: bool, error_str: str).
    """
    subprocess.run(
        [adb_path, "-s", f"{ip}:5555", "shell", f'mkdir -p "{dest_dir}"'],
        capture_output=True, timeout=15, creationflags=_NO_WINDOW,
    )
    filename = Path(file_path).name
    remote_file = dest_dir.rstrip("/") + "/" + filename
    try:
        local_size = os.path.getsize(file_path)
    except OSError:
        local_size = 0

    stop = threading.Event()

    def _poll():
        progress_cb(0)
        while not stop.wait(3.0):
            try:
                r = subprocess.run(
                    [adb_path, "-s", f"{ip}:5555", "shell",
                     f'stat -c%s "{remote_file}" 2>/dev/null || echo 0'],
                    capture_output=True, text=True, timeout=5, creationflags=_NO_WINDOW,
                )
                out = r.stdout.strip()
                if out and out.isdigit() and local_size > 0:
                    pct = min(99, int(int(out) / local_size * 100))
                    progress_cb(pct)
            except Exception:
                pass

    t = threading.Thread(target=_poll, daemon=True)
    t.start()
    try:
        proc = subprocess.Popen(
            [adb_path, "-s", f"{ip}:5555", "push", file_path, dest_dir],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=_NO_WINDOW,
        )
        proc.wait()
        stop.set()
        t.join(timeout=8)
        if proc.returncode == 0:
            progress_cb(100)
            return True, ""
        err = (proc.stdout.read() if proc.stdout else b"").decode("utf-8", errors="replace").strip()
        return False, err or f"adb exit {proc.returncode}"
    except Exception as e:
        stop.set()
        return False, str(e)


def delete_file_from_device(adb_path, ip, remote_file):
    """
    Check file exists then delete it.
    Returns ("deleted" | "not_found" | "error", message).
    """
    try:
        r = subprocess.run(
            [adb_path, "-s", f"{ip}:5555", "shell",
             f'test -f "{remote_file}" && echo EXISTS || echo NOT_FOUND'],
            capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW,
        )
        if r.stdout.strip() == "NOT_FOUND":
            return "not_found", ""
        r2 = subprocess.run(
            [adb_path, "-s", f"{ip}:5555", "shell", f'rm "{remote_file}"'],
            capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW,
        )
        return ("deleted" if r2.returncode == 0 else "error"), ""
    except Exception as e:
        return "error", str(e)


def install_apk_on_device(adb_path, ip, apk_path):
    """Returns (success: bool, message: str)."""
    try:
        r = subprocess.run(
            [adb_path, "-s", f"{ip}:5555", "install", "-r", "-g", apk_path],
            capture_output=True, text=True, timeout=180, creationflags=_NO_WINDOW,
        )
        combined = (r.stdout + r.stderr).strip()
        if r.returncode == 0 and "success" in combined.lower():
            return True, combined
        reason = combined.splitlines()[-1] if combined else "Unknown error"
        return False, reason
    except Exception as e:
        return False, str(e)


def list_device_files(adb_path, ip, dest_dir):
    """
    List files in dest_dir on the device.
    Returns list of (filename, size_str). Raises RuntimeError on failure.
    """
    r = subprocess.run(
        [adb_path, "-s", f"{ip}:5555", "shell", f'ls -la "{dest_dir}/"'],
        capture_output=True, timeout=15, creationflags=_NO_WINDOW,
    )
    output = (r.stdout or b"").decode("utf-8", errors="replace").strip()
    if not output:
        stderr = (r.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(stderr or "Folder is empty or does not exist")

    files = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line[0] != "-":
            continue
        tokens = line.split()
        if len(tokens) < 8:
            continue
        try:
            size_str = human_size(int(tokens[4]))
        except ValueError:
            size_str = tokens[4]
        # ISO date (YYYY-MM-DD) → 7 prefix fields; month-name date → 8 prefix fields.
        # Re-split with exact maxsplit so the last token captures filenames with spaces.
        date_field = tokens[5]
        if len(date_field) == 10 and date_field[4] == "-" and date_field[7] == "-":
            parts = line.split(None, 7)
            filename = parts[7].strip() if len(parts) >= 8 else ""
        else:
            parts = line.split(None, 8)
            filename = parts[8].strip() if len(parts) >= 9 else ""
        if filename:
            files.append((filename, size_str))
    return files


# ---------------------------------------------------------------------------
# Path discovery
# ---------------------------------------------------------------------------

def discover_showtime_path(adb_path, ip):
    """Returns the Showtime VR path string, or None if not found."""
    serial = f"{ip}:5555"
    known = "/sdcard/Showtime VR/Videos/3D"
    try:
        r = subprocess.run(
            [adb_path, "-s", serial, "shell",
             f'test -d "{known}" && echo EXISTS || echo NOT_FOUND'],
            capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW,
        )
        if r.stdout.strip() == "EXISTS":
            return known
        r = subprocess.run(
            [adb_path, "-s", serial, "shell",
             "find /sdcard -maxdepth 4 -type d -iname 'showtime*' 2>/dev/null"],
            capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW,
        )
        for candidate in [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]:
            for sub in (f"{candidate}/Videos/3D", f"{candidate}/Videos", candidate):
                r2 = subprocess.run(
                    [adb_path, "-s", serial, "shell",
                     f'test -d "{sub}" && echo EXISTS || echo NOT_FOUND'],
                    capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
                )
                if r2.stdout.strip() == "EXISTS":
                    return sub
    except Exception:
        pass
    return None
