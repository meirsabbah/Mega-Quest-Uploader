#!/usr/bin/env python3
"""Quest Mass Uploader — push/delete video files on Quest headsets simultaneously over WiFi."""

VERSION = "1.0.6"

import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import sv_ttk
import subprocess
import threading
import socket
import ipaddress
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


# Suppress console windows when spawning ADB subprocesses on Windows
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _resource_dir():
    """Return the directory next to the exe (PyInstaller) or the script."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _human_size(b):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


class QuestUploader:
    def __init__(self, root):
        self.root = root
        self.root.title(f"Quest Mass Uploader  v{VERSION}")
        self.root.geometry("1060x720")
        self.root.minsize(860, 540)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.adb_path = self._find_adb()

        # WiFi tab state
        self.wifi_devices = {}      # ip -> {name, status, item_id}
        self.file_paths = []
        self.busy = False           # True during upload or delete
        self.scanning = False

        # USB tab state
        self.usb_devices = {}       # serial -> {name, state, status, item_id}
        self.auto_enable_var = tk.BooleanVar(value=True)

        self._setup_ui()
        self._check_adb()

    # ------------------------------------------------------------------
    # ADB detection
    # ------------------------------------------------------------------

    def _find_adb(self):
        base = _resource_dir()
        username = os.environ.get("USERNAME", os.environ.get("USER", ""))
        candidates = [
            str(base / "ADB" / "adb.exe"),
            "adb",
            rf"C:\Users\{username}\AppData\Local\Android\Sdk\platform-tools\adb.exe",
            r"C:\Program Files\Android\android-sdk\platform-tools\adb.exe",
            r"C:\Program Files (x86)\Android\android-sdk\platform-tools\adb.exe",
        ]
        for c in candidates:
            try:
                r = subprocess.run([c, "version"], capture_output=True, timeout=5, creationflags=_NO_WINDOW)
                if r.returncode == 0:
                    return c
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        return None

    def _check_adb(self):
        if self.adb_path:
            self.adb_label.config(text="ADB: Ready", foreground="#00bc8c")
        else:
            self.adb_label.config(
                text="ADB not found — place the ADB folder next to this script",
                foreground="#e74c3c",
            )

    # ------------------------------------------------------------------
    # Root UI
    # ------------------------------------------------------------------

    def _setup_ui(self):
        sv_ttk.set_theme("dark")
        style = ttk.Style()
        # Windows 11 accent button for the primary Upload action
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("TNotebook.Tab", padding=(12, 5))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        top = ttk.Frame(self.root, padding=(12, 8))
        top.grid(row=0, column=0, sticky="ew")
        self.adb_label = ttk.Label(top, text="ADB: Checking...")
        self.adb_label.pack(side=tk.LEFT)
        ttk.Label(top, text=f"v{VERSION}", foreground="#888").pack(side=tk.RIGHT)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        usb_tab  = ttk.Frame(self.notebook, padding=10)
        wifi_tab = ttk.Frame(self.notebook)
        self.notebook.add(usb_tab,  text="   USB Setup   ")
        self.notebook.add(wifi_tab, text="   WiFi Upload   ")

        self._setup_usb_tab(usb_tab)
        self._setup_wifi_tab(wifi_tab)

    # ------------------------------------------------------------------
    # USB Setup tab
    # ------------------------------------------------------------------

    def _setup_usb_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        ttk.Label(
            parent,
            text=(
                "Plug headsets in via USB. Accept the 'Allow USB Debugging' prompt on each "
                "headset. Once enabled, the headset is discoverable over WiFi — safe to unplug."
            ),
            wraplength=800, justify=tk.LEFT,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        lf = ttk.LabelFrame(parent, text="USB Connected Devices", padding=5)
        lf.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)

        cols = ("Serial", "Device Name", "Status")
        self.usb_tree = ttk.Treeview(lf, columns=cols, show="headings")
        self.usb_tree.heading("Serial",      text="Serial / ID")
        self.usb_tree.heading("Device Name", text="Device Name")
        self.usb_tree.heading("Status",      text="Status")
        self.usb_tree.column("Serial",      width=200, minwidth=150)
        self.usb_tree.column("Device Name", width=220, minwidth=150)
        self.usb_tree.column("Status",      width=380, minwidth=200)
        self.usb_tree.tag_configure("ready",    background="#1a3d2b", foreground="#00bc8c")
        self.usb_tree.tag_configure("enabling", background="#3d2e00", foreground="#f39c12")
        self.usb_tree.tag_configure("unauth",   background="#3d2e00", foreground="#f39c12")
        self.usb_tree.tag_configure("error",    background="#3d1010", foreground="#e74c3c")

        vsc = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=self.usb_tree.yview)
        self.usb_tree.configure(yscrollcommand=vsc.set)
        self.usb_tree.grid(row=0, column=0, sticky="nsew")
        vsc.grid(row=0, column=1, sticky="ns")

        ctrl = ttk.Frame(parent)
        ctrl.grid(row=2, column=0, sticky="ew")
        ctrl.columnconfigure(0, weight=1)
        ttk.Checkbutton(
            ctrl,
            text="Automatically enable WiFi ADB when a headset is plugged in",
            variable=self.auto_enable_var,
        ).grid(row=0, column=0, sticky="w")
        bf = ttk.Frame(ctrl)
        bf.grid(row=0, column=1, sticky="e")
        ttk.Button(bf, text="Enable All",     command=self._enable_all_usb, width=14).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(bf, text="Detect Devices", command=self._refresh_usb,   width=16).pack(side=tk.RIGHT)

        self.usb_status_label = ttk.Label(parent, text="Plug in headsets, then click 'Detect Devices'.")
        self.usb_status_label.grid(row=3, column=0, sticky="w", pady=(6, 0))

    def _get_showtime_name(self, serial):
        """Read the device name from Showtime VR's config.txt. Returns None if unavailable."""
        try:
            r = subprocess.run(
                [self.adb_path, "-s", serial, "shell", 'cat "/sdcard/Showtime VR/config.txt"'],
                capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
            )
            content = r.stdout.strip()
            if not content:
                return None

            # Parse "key = value" lines — format used by Showtime VR config.txt
            for line in content.splitlines():
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

    def _get_usb_devices_raw(self):
        r = subprocess.run([self.adb_path, "devices"], capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW)
        result = {}
        for line in r.stdout.splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and ":" not in parts[0]:
                result[parts[0]] = parts[1]
        return result

    def _get_device_info_usb(self, serial):
        """Return (model, device_name) for a USB-connected device."""
        try:
            model_r = subprocess.run(
                [self.adb_path, "-s", serial, "shell", "getprop ro.product.model"],
                capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
            )
            model = model_r.stdout.strip() or "Unknown"

            name = self._get_showtime_name(serial) or model
            return model, name
        except Exception:
            return "Unknown", "Unknown"

    def _on_usb_appeared(self, serial, state):
        if serial in self.usb_devices:
            return
        if state == "unauthorized":
            name   = "—"
            status, tag = "Waiting — accept USB Debugging on the headset", "unauth"
        else:
            _, name = self._get_device_info_usb(serial)
            status, tag = "Connected", ""

        item_id = self.usb_tree.insert("", tk.END, values=(serial, name, status),
                                        tags=(tag,) if tag else ())
        self.usb_devices[serial] = {"name": name, "state": state, "status": status, "item_id": item_id}
        self.usb_status_label.config(text=f"{len(self.usb_devices)} device(s) connected via USB.")

        if state == "device" and self.auto_enable_var.get():
            threading.Thread(target=self._enable_wifi_adb, args=(serial,), daemon=True).start()

    def _on_usb_authorized(self, serial):
        if serial not in self.usb_devices:
            return
        _, name = self._get_device_info_usb(serial)
        self.usb_devices[serial].update({"state": "device", "name": name})
        self._update_usb_row(serial, name, "Authorized — enabling WiFi ADB...", "enabling")
        if self.auto_enable_var.get():
            threading.Thread(target=self._enable_wifi_adb, args=(serial,), daemon=True).start()

    def _on_usb_removed(self, serial):
        if serial not in self.usb_devices:
            return
        self.usb_tree.delete(self.usb_devices[serial]["item_id"])
        del self.usb_devices[serial]
        self.usb_status_label.config(
            text=f"{len(self.usb_devices)} device(s) connected via USB."
            if self.usb_devices else "Monitoring for USB connections..."
        )

    def _enable_wifi_adb(self, serial):
        name = self.usb_devices.get(serial, {}).get("name", "—")
        self.root.after(0, self._update_usb_row, serial, name, "Enabling WiFi ADB...", "enabling")
        try:
            r = subprocess.run(
                [self.adb_path, "-s", serial, "tcpip", "5555"],
                capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW,
            )
            if r.returncode == 0:
                self.root.after(0, self._update_usb_row, serial, name,
                                "WiFi ADB enabled — safe to unplug", "ready")
            else:
                self.root.after(0, self._update_usb_row, serial, name,
                                f"Failed: {(r.stderr or r.stdout).strip()}", "error")
        except Exception as e:
            self.root.after(0, self._update_usb_row, serial, name, f"Error: {e}", "error")

    def _update_usb_row(self, serial, name, status, tag):
        if serial not in self.usb_devices:
            return
        self.usb_devices[serial]["status"] = status
        self.usb_tree.item(self.usb_devices[serial]["item_id"],
                            values=(serial, name, status), tags=(tag,) if tag else ())

    def _refresh_usb(self):
        if self.adb_path:
            threading.Thread(target=self._refresh_usb_worker, daemon=True).start()

    def _refresh_usb_worker(self):
        current = self._get_usb_devices_raw()
        for serial, state in current.items():
            if serial not in self.usb_devices:
                self.root.after(0, self._on_usb_appeared, serial, state)
        for serial in list(self.usb_devices.keys()):
            if serial not in current:
                self.root.after(0, self._on_usb_removed, serial)

    def _enable_all_usb(self):
        targets = [s for s, d in self.usb_devices.items() if d["state"] == "device"]
        if not targets:
            messagebox.showinfo("Nothing to enable",
                                "No authorized devices connected.\n"
                                "Accept the USB Debugging prompt on each headset first.")
            return
        for serial in targets:
            threading.Thread(target=self._enable_wifi_adb, args=(serial,), daemon=True).start()

    # ------------------------------------------------------------------
    # WiFi Upload tab
    # ------------------------------------------------------------------

    def _setup_wifi_tab(self, parent):
        # Two-column layout: device list on the left, controls on the right
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=0)   # right panel natural width
        parent.rowconfigure(0, weight=1)

        # ── Left panel: device list ────────────────────────────────────
        left = ttk.Frame(parent, padding=(8, 8, 4, 8))
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        scan_bar = ttk.Frame(left)
        scan_bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        scan_bar.columnconfigure(1, weight=1)
        self.scan_btn = ttk.Button(scan_bar, text="Scan Network",
                                    command=self.start_scan, width=16)
        self.scan_btn.grid(row=0, column=0)
        self.device_count_label = ttk.Label(scan_bar, text="Devices found: 0",
                                             foreground="#555")
        self.device_count_label.grid(row=0, column=1, sticky="e")

        lf = ttk.LabelFrame(left, text="Connected Devices", padding=4)
        lf.grid(row=1, column=0, sticky="nsew")
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)

        cols = ("IP Address", "Device Name", "Status")
        self.tree = ttk.Treeview(lf, columns=cols, show="headings",
                                  selectmode="extended")
        self.tree.heading("IP Address",  text="IP Address")
        self.tree.heading("Device Name", text="Device Name")
        self.tree.heading("Status",      text="Status")
        self.tree.column("IP Address",  width=130, minwidth=100)
        self.tree.column("Device Name", width=200, minwidth=130)
        self.tree.column("Status",      width=340, minwidth=180)
        for tag, bg, fg in (
            ("done",      "#1a3d2b", "#00bc8c"),
            ("skipped",   "#2a2a2a", "#888888"),
            ("uploading", "#3d2e00", "#f39c12"),
            ("checking",  "#0d2233", "#3498db"),
            ("error",     "#3d1010", "#e74c3c"),
        ):
            self.tree.tag_configure(tag, background=bg, foreground=fg)
        self.tree.bind("<<TreeviewSelect>>", self._on_selection_change)
        self.tree.bind("<Double-1>", self._on_device_double_click)
        vsc = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsc.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsc.grid(row=0, column=1, sticky="ns")

        sel_bar = ttk.Frame(left)
        sel_bar.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        ttk.Button(sel_bar, text="Select All",   command=self._select_all,
                   width=13).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(sel_bar, text="Deselect All", command=self._deselect_all,
                   width=13).pack(side=tk.LEFT)
        self.selected_label = ttk.Label(sel_bar, text="Selected: 0 / 0",
                                         foreground="#555")
        self.selected_label.pack(side=tk.RIGHT)

        prog_frame = ttk.Frame(left)
        prog_frame.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        prog_frame.columnconfigure(0, weight=1)
        self.progress_var = tk.DoubleVar()
        ttk.Progressbar(prog_frame, variable=self.progress_var,
                        maximum=100).grid(
            row=0, column=0, sticky="ew", pady=(0, 4))
        self.status_label = ttk.Label(
            prog_frame, text="Ready — scan the network to find devices.",
            foreground="#555")
        self.status_label.grid(row=1, column=0, sticky="w")

        # ── Right panel: operations ────────────────────────────────────
        right = ttk.Frame(parent, padding=(4, 8, 8, 8))
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        r = 0

        # Files to upload
        files_frame = ttk.LabelFrame(right, text="Files to Upload", padding=6)
        files_frame.grid(row=r, column=0, sticky="ew"); r += 1
        files_frame.columnconfigure(0, weight=1)
        self.file_listbox = tk.Listbox(
            files_frame, height=3, selectmode=tk.EXTENDED, activestyle="none",
            background="#2d2d2d",
            foreground="#ffffff",
            selectbackground="#0078d4",
            selectforeground="#ffffff",
            relief="flat", highlightthickness=1,
            highlightbackground="#3d3d3d",
            highlightcolor="#0078d4",
        )
        hsc = ttk.Scrollbar(files_frame, orient=tk.HORIZONTAL,
                             command=self.file_listbox.xview)
        self.file_listbox.configure(xscrollcommand=hsc.set)
        self.file_listbox.grid(row=0, column=0, sticky="ew")
        hsc.grid(row=1, column=0, sticky="ew")
        fb = ttk.Frame(files_frame)
        fb.grid(row=0, column=1, sticky="n", padx=(4, 0))
        ttk.Button(fb, text="Add",    command=self._add_files,             width=8).pack(pady=1)
        ttk.Button(fb, text="Remove", command=self._remove_selected_files,  width=8).pack(pady=1)
        ttk.Button(fb, text="Clear",  command=self._clear_files,            width=8).pack(pady=1)

        # Primary upload action
        self.upload_btn = ttk.Button(right, text="Upload to Selected Devices",
                                      command=self.start_upload,
                                      state=tk.DISABLED, style="Accent.TButton")
        self.upload_btn.grid(row=r, column=0, sticky="ew", pady=(6, 10)); r += 1

        ttk.Separator(right, orient=tk.HORIZONTAL).grid(
            row=r, column=0, sticky="ew", pady=(0, 8)); r += 1

        # Delete file
        del_frame = ttk.LabelFrame(right, text="Delete File from Devices", padding=6)
        del_frame.grid(row=r, column=0, sticky="ew"); r += 1
        del_frame.columnconfigure(0, weight=1)
        self.delete_var = tk.StringVar()
        ttk.Entry(del_frame, textvariable=self.delete_var).grid(
            row=0, column=0, sticky="ew", pady=(0, 4))
        self.delete_btn = ttk.Button(del_frame,
                                      text="Delete from Selected Devices",
                                      command=self._delete_from_devices)
        self.delete_btn.grid(row=1, column=0, sticky="ew")

        ttk.Separator(right, orient=tk.HORIZONTAL).grid(
            row=r, column=0, sticky="ew", pady=8); r += 1

        # APK installation
        apk_frame = ttk.LabelFrame(right, text="APK Installation", padding=6)
        apk_frame.grid(row=r, column=0, sticky="ew"); r += 1
        apk_frame.columnconfigure(0, weight=1)
        self.apk_var = tk.StringVar()
        apk_top = ttk.Frame(apk_frame)
        apk_top.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        apk_top.columnconfigure(0, weight=1)
        self.apk_combo = ttk.Combobox(apk_top, textvariable=self.apk_var,
                                       state="readonly")
        self.apk_combo.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(apk_top, text="Refresh", command=self._scan_apk_folder,
                   width=8).grid(row=0, column=1, padx=(0, 2))
        ttk.Button(apk_top, text="Browse",  command=self._browse_apk,
                   width=8).grid(row=0, column=2)
        self.install_btn = ttk.Button(apk_frame,
                                       text="Install on Selected Devices",
                                       command=self._install_apk)
        self.install_btn.grid(row=1, column=0, sticky="ew")

        ttk.Separator(right, orient=tk.HORIZONTAL).grid(
            row=r, column=0, sticky="ew", pady=8); r += 1

        # Options
        opt_frame = ttk.LabelFrame(right, text="Options", padding=6)
        opt_frame.grid(row=r, column=0, sticky="ew"); r += 1
        opt_frame.columnconfigure(1, weight=1)
        ttk.Label(opt_frame, text="Destination:").grid(
            row=0, column=0, sticky="w", pady=(0, 4))
        self.dest_var = tk.StringVar(value="/sdcard/Showtime VR/Videos/3D")
        ttk.Entry(opt_frame, textvariable=self.dest_var).grid(
            row=0, column=1, sticky="ew", padx=(4, 4), pady=(0, 4))
        self.discover_btn = ttk.Button(opt_frame, text="Discover",
                                        command=self._discover_path, width=9)
        self.discover_btn.grid(row=0, column=2, pady=(0, 4))
        ttk.Label(opt_frame, text="Concurrent:").grid(row=1, column=0, sticky="w")
        self.batch_var = tk.IntVar(value=30)
        b_row = ttk.Frame(opt_frame)
        b_row.grid(row=1, column=1, columnspan=2, sticky="w", padx=4)
        ttk.Spinbox(b_row, from_=1, to=300, textvariable=self.batch_var,
                    width=5).pack(side=tk.LEFT)
        ttk.Label(b_row, text="simultaneous").pack(side=tk.LEFT, padx=4)

        self._scan_apk_folder()

    # ------------------------------------------------------------------
    # File management
    # ------------------------------------------------------------------

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select Video Files",
            filetypes=[("Video files", "*.mp4 *.mkv *.avi *.mov *.sbs"), ("All files", "*.*")],
        )
        for p in paths:
            if p not in self.file_paths:
                self.file_paths.append(p)
                self.file_listbox.insert(tk.END, p)
        self._refresh_buttons()

    def _remove_selected_files(self):
        for i in reversed(self.file_listbox.curselection()):
            self.file_listbox.delete(i)
            self.file_paths.pop(i)
        self._refresh_buttons()

    def _clear_files(self):
        self.file_listbox.delete(0, tk.END)
        self.file_paths.clear()
        self._refresh_buttons()

    # ------------------------------------------------------------------
    # APK management
    # ------------------------------------------------------------------

    def _scan_apk_folder(self):
        """Populate the APK combobox with any .apk files found in the ADB folder."""
        adb_dir = _resource_dir() / "ADB"
        apks = sorted(adb_dir.glob("*.apk")) if adb_dir.is_dir() else []
        self._apk_paths = {p.name: str(p) for p in apks}
        self.apk_combo["values"] = list(self._apk_paths.keys())
        if self._apk_paths and not self.apk_var.get():
            self.apk_combo.current(0)
        self._refresh_buttons()

    def _browse_apk(self):
        path = filedialog.askopenfilename(
            title="Select APK",
            filetypes=[("Android packages", "*.apk"), ("All files", "*.*")],
        )
        if path:
            name = Path(path).name
            self._apk_paths[name] = path
            values = list(self.apk_combo["values"])
            if name not in values:
                values.append(name)
                self.apk_combo["values"] = values
            self.apk_var.set(name)
            self._refresh_buttons()

    def _get_selected_apk_path(self):
        name = self.apk_var.get().strip()
        return getattr(self, "_apk_paths", {}).get(name)

    def _install_apk(self):
        apk_path = self._get_selected_apk_path()
        if not apk_path:
            messagebox.showerror("No APK", "Select or browse to an APK file first.")
            return
        selected_ips = self._get_selected_ips()
        if not selected_ips:
            messagebox.showerror("No Devices", "Select at least one device.")
            return
        self.busy = True
        self.scan_btn.config(state=tk.DISABLED)
        self._refresh_buttons()
        for ip in selected_ips:
            self._set_wifi_status(ip, "Waiting to install...", "")
        self.progress_var.set(0)
        threading.Thread(
            target=self._install_worker,
            args=(selected_ips, apk_path),
            daemon=True,
        ).start()

    def _install_one(self, ip, apk_path, counter, total, lock):
        serial = f"{ip}:5555"
        apk_name = Path(apk_path).name
        self.root.after(0, self._set_wifi_status, ip, f"Installing {apk_name}...", "uploading")
        try:
            r = subprocess.run(
                [self.adb_path, "-s", serial, "install", "-r", "-g", apk_path],
                capture_output=True, text=True, timeout=180, creationflags=_NO_WINDOW,
            )
            combined = (r.stdout + r.stderr).strip()
            if r.returncode == 0 and "success" in combined.lower():
                self.root.after(0, self._set_wifi_status, ip, f"Installed: {apk_name}", "done")
            else:
                reason = combined.splitlines()[-1] if combined else "Unknown error"
                self.root.after(0, self._set_wifi_status, ip, f"Failed: {reason}", "error")
        except Exception as e:
            self.root.after(0, self._set_wifi_status, ip, f"Error: {e}", "error")
        self._tick_progress(counter, total, lock, "processed")

    def _install_worker(self, selected_ips, apk_path):
        counter = [0]
        lock = threading.Lock()
        with ThreadPoolExecutor(max_workers=self.batch_var.get()) as ex:
            for ip in selected_ips:
                ex.submit(self._install_one, ip, apk_path, counter, len(selected_ips), lock)
        self.root.after(0, self._operation_done, "Install")

    # ------------------------------------------------------------------
    # Device selection
    # ------------------------------------------------------------------

    def _select_all(self):
        self.tree.selection_set(self.tree.get_children())
        self._on_selection_change()

    def _deselect_all(self):
        self.tree.selection_remove(self.tree.get_children())
        self._on_selection_change()

    def _on_selection_change(self, *_):
        selected = len(self.tree.selection())
        total = len(self.tree.get_children())
        self.selected_label.config(text=f"Selected: {selected} / {total}")
        self._refresh_buttons()

    def _get_selected_ips(self):
        item_to_ip = {d["item_id"]: ip for ip, d in self.wifi_devices.items()}
        return [item_to_ip[iid] for iid in self.tree.selection() if iid in item_to_ip]

    def _refresh_buttons(self):
        has_devices = bool(self._get_selected_ips())
        idle = not self.busy and not self.scanning
        self.upload_btn.config(state=tk.NORMAL if (has_devices and self.file_paths and idle) else tk.DISABLED)
        self.delete_btn.config(state=tk.NORMAL if (has_devices and idle) else tk.DISABLED)
        has_apk = bool(getattr(self, "_apk_paths", {}) and self.apk_var.get())
        self.install_btn.config(state=tk.NORMAL if (has_devices and has_apk and idle) else tk.DISABLED)

    # keep old name as alias so nothing else breaks
    _refresh_upload_btn = _refresh_buttons

    # ------------------------------------------------------------------
    # Path discovery
    # ------------------------------------------------------------------

    def _discover_path(self):
        ips = self._get_selected_ips() or list(self.wifi_devices.keys())
        if not ips:
            messagebox.showwarning("No Devices", "Scan the network first.")
            return
        self.discover_btn.config(state=tk.DISABLED, text="Discovering...")
        self.status_label.config(text=f"Discovering Showtime VR path from {ips[0]}...")
        threading.Thread(target=self._discover_path_worker, args=(ips[0],), daemon=True).start()

    def _discover_path_worker(self, ip):
        serial = f"{ip}:5555"
        found = None
        try:
            known = "/sdcard/Showtime VR/Videos/3D"
            r = subprocess.run(
                [self.adb_path, "-s", serial, "shell",
                 f'test -d "{known}" && echo EXISTS || echo NOT_FOUND'],
                capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW,
            )
            if r.stdout.strip() == "EXISTS":
                found = known
            else:
                r = subprocess.run(
                    [self.adb_path, "-s", serial, "shell",
                     "find /sdcard -maxdepth 4 -type d -iname 'showtime*' 2>/dev/null"],
                    capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW,
                )
                for c in [l.strip() for l in r.stdout.splitlines() if l.strip()]:
                    for sub in (f"{c}/Videos/3D", f"{c}/Videos", c):
                        r2 = subprocess.run(
                            [self.adb_path, "-s", serial, "shell",
                             f'test -d "{sub}" && echo EXISTS || echo NOT_FOUND'],
                            capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
                        )
                        if r2.stdout.strip() == "EXISTS":
                            found = sub
                            break
                    if found:
                        break
        except Exception as e:
            self.root.after(0, lambda err=e: self.status_label.config(text=f"Discovery error: {err}"))
            self.root.after(0, self.discover_btn.config, {"state": tk.NORMAL, "text": "Discover"})
            return

        if found:
            self.root.after(0, self.dest_var.set, found)
            self.root.after(0, lambda p=found: self.status_label.config(text=f"Path found: {p}"))
        else:
            self.root.after(0, lambda: messagebox.showwarning(
                "Not Found", "Could not locate a Showtime VR folder.\nEnter the path manually."))
            self.root.after(0, self.status_label.config, {"text": "Path not found — enter manually."})
        self.root.after(0, self.discover_btn.config, {"state": tk.NORMAL, "text": "Discover"})

    # ------------------------------------------------------------------
    # Network scan
    # ------------------------------------------------------------------

    def _get_local_subnet(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
        finally:
            s.close()
        return local_ip.rsplit(".", 1)[0] + ".0/24"

    def start_scan(self):
        if not self.adb_path:
            messagebox.showerror("ADB Not Found", "ADB not found.")
            return
        if self.scanning or self.busy:
            return
        self.scanning = True
        self.scan_btn.config(state=tk.DISABLED, text="Scanning...")
        self.upload_btn.config(state=tk.DISABLED)
        self.delete_btn.config(state=tk.DISABLED)
        self.tree.delete(*self.tree.get_children())
        self.wifi_devices.clear()
        self.device_count_label.config(text="Devices found: 0")
        self.selected_label.config(text="Selected: 0 / 0")
        self.status_label.config(text="Scanning network for devices on port 5555...")
        self.progress_var.set(0)
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        try:
            hosts = list(ipaddress.IPv4Network(self._get_local_subnet(), strict=False).hosts())

            def probe(ip):
                ip_str = str(ip)
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(0.5)
                    open_ = sock.connect_ex((ip_str, 5555)) == 0
                    sock.close()
                    if not open_:
                        return
                    r = subprocess.run(
                        [self.adb_path, "connect", f"{ip_str}:5555"],
                        capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
                    )
                    if "connected" not in r.stdout.lower():
                        return
                    model_r = subprocess.run(
                        [self.adb_path, "-s", f"{ip_str}:5555", "shell",
                         "getprop ro.product.model"],
                        capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
                    )
                    model = model_r.stdout.strip() or "Unknown"
                    name = self._get_showtime_name(f"{ip_str}:5555") or model
                    self.root.after(0, self._add_wifi_device, ip_str, name)
                except Exception:
                    pass

            with ThreadPoolExecutor(max_workers=100) as ex:
                ex.map(probe, hosts)
        finally:
            self.root.after(0, self._scan_done)

    def _add_wifi_device(self, ip, name):
        item_id = self.tree.insert("", tk.END, values=(ip, name, "Ready"))
        self.wifi_devices[ip] = {"name": name, "status": "Ready", "item_id": item_id}
        self.tree.selection_add(item_id)
        total    = len(self.tree.get_children())
        selected = len(self.tree.selection())
        self.device_count_label.config(text=f"Devices found: {total}")
        self.selected_label.config(text=f"Selected: {selected} / {total}")

    def _scan_done(self):
        self.scanning = False
        self.scan_btn.config(state=tk.NORMAL, text="Scan Network")
        self.status_label.config(text=f"Scan complete — {len(self.wifi_devices)} device(s) found.")
        self._refresh_buttons()

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def start_upload(self):
        selected_ips = self._get_selected_ips()
        if not selected_ips or not self.file_paths:
            return
        self.busy = True
        self.scan_btn.config(state=tk.DISABLED)
        self._refresh_buttons()
        for ip in selected_ips:
            self._set_wifi_status(ip, "Waiting...", "")
        self.progress_var.set(0)
        threading.Thread(
            target=self._upload_worker,
            args=(selected_ips, list(self.file_paths)),
            daemon=True,
        ).start()

    def _file_exists_on_device(self, ip, remote_file, local_size):
        try:
            r = subprocess.run(
                [self.adb_path, "-s", f"{ip}:5555", "shell",
                 f'stat -c%s "{remote_file}" 2>/dev/null || echo NOT_FOUND'],
                capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW,
            )
            out = r.stdout.strip()
            return out != "NOT_FOUND" and int(out) == local_size
        except Exception:
            return False

    def _upload_one(self, ip, file_paths, dest_dir, file_sizes, counter, total, lock):
        n = len(file_paths)
        uploaded = skipped = errors = 0
        last_error = ""

        # Ensure destination directory exists before any push
        subprocess.run(
            [self.adb_path, "-s", f"{ip}:5555", "shell", f'mkdir -p "{dest_dir}"'],
            capture_output=True, timeout=15, creationflags=_NO_WINDOW,
        )

        for i, file_path in enumerate(file_paths):
            filename = Path(file_path).name
            remote_file = dest_dir.rstrip("/") + "/" + filename
            prefix = f"[{i+1}/{n}] {filename}: " if n > 1 else f"{filename}: "

            self.root.after(0, self._set_wifi_status, ip, f"{prefix}Checking...", "checking")
            if self._file_exists_on_device(ip, remote_file, file_sizes[i]):
                skipped += 1
                self.root.after(0, self._set_wifi_status, ip, f"{prefix}Already on device — skipped", "skipped")
                continue

            self.root.after(0, self._set_wifi_status, ip, f"{prefix}Starting upload...", "uploading")
            try:
                proc = subprocess.Popen(
                    [self.adb_path, "-s", f"{ip}:5555", "push", file_path, dest_dir],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
                    creationflags=_NO_WINDOW,
                )
                for raw_line in proc.stdout:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if line.startswith("[") and "%" in line:
                        try:
                            pct = int(line.split("%")[0].strip("[").strip())
                            self.root.after(0, self._set_wifi_status, ip,
                                            f"{prefix}Uploading {pct}%", "uploading")
                        except ValueError:
                            pass
                proc.wait()
                if proc.returncode == 0:
                    uploaded += 1
                    self.root.after(0, self._set_wifi_status, ip, f"{prefix}Done", "done")
                else:
                    errors += 1
                    last_error = f"adb exit {proc.returncode}"
                    self.root.after(0, self._set_wifi_status, ip, f"{prefix}Transfer failed (exit {proc.returncode})", "error")
            except Exception as e:
                errors += 1
                last_error = str(e)
                self.root.after(0, self._set_wifi_status, ip, f"{prefix}Error — {e}", "error")

        parts = []
        if uploaded: parts.append(f"{uploaded} uploaded")
        if skipped:  parts.append(f"{skipped} already on device")
        if errors:
            err_txt = f"{errors} failed"
            if last_error:
                err_txt += f" ({last_error})"
            parts.append(err_txt)
        final = " | ".join(parts) if parts else "Done"
        self.root.after(0, self._set_wifi_status, ip, final,
                        "error" if errors else ("skipped" if not uploaded else "done"))
        self._tick_progress(counter, total, lock, "uploaded")

    def _upload_worker(self, selected_ips, file_paths):
        dest_dir   = self.dest_var.get().strip()
        file_sizes = [os.path.getsize(p) for p in file_paths]
        counter = [0]
        lock = threading.Lock()
        with ThreadPoolExecutor(max_workers=self.batch_var.get()) as ex:
            for ip in selected_ips:
                ex.submit(self._upload_one, ip, file_paths, dest_dir,
                          file_sizes, counter, len(selected_ips), lock)
        self.root.after(0, self._operation_done, "Upload")

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def _delete_from_devices(self):
        filename = self.delete_var.get().strip()
        if not filename:
            messagebox.showerror("No Filename", "Enter the exact filename to delete (e.g. movie.mp4).")
            return
        selected_ips = self._get_selected_ips()
        if not selected_ips:
            messagebox.showerror("No Devices", "Select at least one device.")
            return
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Delete  '{filename}'  from {len(selected_ips)} device(s)?\n\nThis cannot be undone.",
        ):
            return
        self.busy = True
        self.scan_btn.config(state=tk.DISABLED)
        self._refresh_buttons()
        for ip in selected_ips:
            self._set_wifi_status(ip, "Waiting to delete...", "")
        self.progress_var.set(0)
        dest_dir    = self.dest_var.get().strip()
        remote_file = dest_dir.rstrip("/") + "/" + filename
        threading.Thread(
            target=self._delete_worker,
            args=(selected_ips, remote_file, filename),
            daemon=True,
        ).start()

    def _delete_one(self, ip, remote_file, filename, counter, total, lock):
        serial = f"{ip}:5555"
        self.root.after(0, self._set_wifi_status, ip, f"Checking for {filename}...", "checking")
        try:
            r = subprocess.run(
                [self.adb_path, "-s", serial, "shell",
                 f'test -f "{remote_file}" && echo EXISTS || echo NOT_FOUND'],
                capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW,
            )
            if r.stdout.strip() == "NOT_FOUND":
                self.root.after(0, self._set_wifi_status, ip, "File not found — skipped", "skipped")
            else:
                r2 = subprocess.run(
                    [self.adb_path, "-s", serial, "shell", f'rm "{remote_file}"'],
                    capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW,
                )
                if r2.returncode == 0:
                    self.root.after(0, self._set_wifi_status, ip, f"Deleted: {filename}", "done")
                else:
                    self.root.after(0, self._set_wifi_status, ip, "Delete failed", "error")
        except Exception as e:
            self.root.after(0, self._set_wifi_status, ip, f"Error: {e}", "error")
        self._tick_progress(counter, total, lock, "deleted")

    def _delete_worker(self, selected_ips, remote_file, filename):
        counter = [0]
        lock = threading.Lock()
        with ThreadPoolExecutor(max_workers=self.batch_var.get()) as ex:
            for ip in selected_ips:
                ex.submit(self._delete_one, ip, remote_file, filename,
                          counter, len(selected_ips), lock)
        self.root.after(0, self._operation_done, "Delete")

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _tick_progress(self, counter, total, lock, verb):
        with lock:
            counter[0] += 1
            done = counter[0]
            pct  = (done / total) * 100
        self.root.after(0, self.progress_var.set, pct)
        self.root.after(0, lambda d=done: self.status_label.config(
            text=f"Progress: {d}/{total} devices {verb}"
        ))

    def _operation_done(self, op_name):
        self.busy = False
        self.scan_btn.config(state=tk.NORMAL)
        self._refresh_buttons()
        statuses = [d["status"] for d in self.wifi_devices.values()]
        if op_name == "Upload":
            ok  = sum(1 for s in statuses if "uploaded" in s or s == "Done")
            skp = sum(1 for s in statuses if "already on device" in s and "uploaded" not in s)
            err = sum(1 for s in statuses if "failed" in s or "Error" in s)
            summary = f"Upload complete — {ok} uploaded, {skp} skipped, {err} errors."
        elif op_name == "Delete":
            ok  = sum(1 for s in statuses if s.startswith("Deleted"))
            skp = sum(1 for s in statuses if "not found" in s)
            err = sum(1 for s in statuses if "failed" in s or "Error" in s)
            summary = f"Delete complete — {ok} deleted, {skp} not found, {err} errors."
        else:  # Install
            ok  = sum(1 for s in statuses if s.startswith("Installed"))
            err = sum(1 for s in statuses if "Failed" in s or "Error" in s)
            summary = f"Install complete — {ok} succeeded, {err} failed."
        self.status_label.config(text=summary)
        messagebox.showinfo(f"{op_name} Complete", summary)

    # ------------------------------------------------------------------
    # Device file browser
    # ------------------------------------------------------------------

    def _on_device_double_click(self, event):
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return
        item_to_ip = {d["item_id"]: ip for ip, d in self.wifi_devices.items()}
        ip = item_to_ip.get(item_id)
        if ip:
            self._open_file_browser(ip, self.wifi_devices[ip]["name"])

    def _open_file_browser(self, ip, device_name):
        dest_dir = self.dest_var.get().strip()

        dlg = tk.Toplevel(self.root)
        dlg.title(f"Files on {device_name}")
        dlg.geometry("640x420")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.columnconfigure(0, weight=1)
        dlg.rowconfigure(1, weight=1)

        ttk.Label(dlg, text=dest_dir, foreground="gray").grid(
            row=0, column=0, sticky="ew", padx=10, pady=(8, 2))

        # File list
        lf = ttk.Frame(dlg, padding=5)
        lf.grid(row=1, column=0, sticky="nsew", padx=10)
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)

        cols = ("Filename", "Size")
        ftree = ttk.Treeview(lf, columns=cols, show="headings", selectmode="extended")
        ftree.heading("Filename", text="Filename")
        ftree.heading("Size",     text="Size")
        ftree.column("Filename", width=460, minwidth=300)
        ftree.column("Size",     width=100, minwidth=80, anchor="e")
        vsc = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=ftree.yview)
        ftree.configure(yscrollcommand=vsc.set)
        ftree.grid(row=0, column=0, sticky="nsew")
        vsc.grid(row=0, column=1, sticky="ns")

        status_lbl = ttk.Label(dlg, text="Loading...")
        status_lbl.grid(row=2, column=0, sticky="w", padx=10, pady=4)

        btn_bar = ttk.Frame(dlg, padding=(10, 4))
        btn_bar.grid(row=3, column=0, sticky="ew")

        def load_files():
            try:
                r = subprocess.run(
                    [self.adb_path, "-s", f"{ip}:5555", "shell",
                     f'ls -la "{dest_dir}/"'],
                    capture_output=True, timeout=15, creationflags=_NO_WINDOW,
                )
                # Decode bytes manually — more reliable than text=True over ADB WiFi on Windows
                output = (r.stdout or b"").decode("utf-8", errors="replace").strip()

                if not output:
                    stderr = (r.stderr or b"").decode("utf-8", errors="replace").strip()
                    msg = stderr or "Folder is empty or does not exist"
                    dlg.after(0, lambda m=msg: _populate([], m))
                    return

                files = []
                for line in output.splitlines():
                    line = line.strip()
                    if not line or line[0] != '-':
                        continue
                    # Unlimited split to inspect fields without discarding anything
                    tokens = line.split()
                    if len(tokens) < 8:
                        continue
                    try:
                        size_str = _human_size(int(tokens[4]))
                    except ValueError:
                        size_str = tokens[4]
                    # Detect date format from field 5 and re-split with the exact maxsplit
                    # so the LAST piece captures the full filename including any spaces.
                    #   ISO  date (YYYY-MM-DD): 7 prefix fields → split(None, 7)  → parts[7]
                    #   Month-name date (Jan…): 8 prefix fields → split(None, 8)  → parts[8]
                    date_field = tokens[5]
                    if len(date_field) == 10 and date_field[4] == '-' and date_field[7] == '-':
                        parts = line.split(None, 7)
                        filename = parts[7].strip() if len(parts) >= 8 else ""
                    else:
                        parts = line.split(None, 8)
                        filename = parts[8].strip() if len(parts) >= 9 else ""
                    if filename:
                        files.append((filename, size_str))

                if files:
                    dlg.after(0, lambda f=files: _populate(f))
                else:
                    # Show the first non-blank line so the format can be diagnosed
                    first = next((l for l in output.splitlines() if l.strip()), "(empty)")
                    dlg.after(0, lambda p=first: _populate(
                        [], f"Could not read files. Raw output: {p}"))
            except Exception as e:
                dlg.after(0, lambda err=str(e): status_lbl.config(text=f"Error: {err}"))

        def _populate(files, status_msg=None):
            ftree.delete(*ftree.get_children())
            for filename, size in files:
                ftree.insert("", tk.END, values=(filename, size))
            status_lbl.config(text=status_msg if status_msg else
                              f"{len(files)} file(s)  —  double-click a file to delete it")

        def refresh():
            status_lbl.config(text="Loading...")
            threading.Thread(target=load_files, daemon=True).start()

        def delete_selected():
            selected = ftree.selection()
            if not selected:
                return
            names = [ftree.item(i, "values")[0] for i in selected]
            if not messagebox.askyesno("Confirm Delete",
                    f"Delete {len(names)} file(s) from {device_name}?", parent=dlg):
                return
            status_lbl.config(text="Deleting...")
            def do_delete(sel=selected, fns=names):
                removed = []
                for item_id, fn in zip(sel, fns):
                    remote = dest_dir.rstrip("/") + "/" + fn
                    r = subprocess.run(
                        [self.adb_path, "-s", f"{ip}:5555", "shell", f'rm "{remote}"'],
                        capture_output=True, timeout=15, creationflags=_NO_WINDOW,
                    )
                    if r.returncode == 0:
                        removed.append(item_id)
                def _apply():
                    for iid in removed:
                        try:
                            ftree.delete(iid)
                        except Exception:
                            pass
                    remaining = len(ftree.get_children())
                    status_lbl.config(
                        text=f"{remaining} file(s)  —  double-click a file to delete it")
                dlg.after(0, _apply)
            threading.Thread(target=do_delete, daemon=True).start()

        def deselect_device():
            item_id = self.wifi_devices[ip]["item_id"]
            self.tree.selection_remove(item_id)
            self._on_selection_change()
            dlg.destroy()

        def copy_filename():
            selected = ftree.selection()
            if not selected:
                return
            filename = ftree.item(selected[0], "values")[0]
            dlg.clipboard_clear()
            dlg.clipboard_append(filename)
            self.delete_var.set(filename)   # also pre-fill the Delete field on main window
            status_lbl.config(text=f'Copied: {filename}')

        ftree.bind("<Double-1>", lambda e: delete_selected())

        ttk.Button(btn_bar, text="Refresh",         command=refresh,         width=10).pack(side=tk.LEFT)
        ttk.Button(btn_bar, text="Delete Selected", command=delete_selected, width=16).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_bar, text="Copy Name",       command=copy_filename,   width=12).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_bar, text="Deselect Device", command=deselect_device, width=16).pack(side=tk.LEFT)
        ttk.Button(btn_bar, text="Close",           command=dlg.destroy,     width=10).pack(side=tk.RIGHT)

        refresh()

    def _set_wifi_status(self, ip, status, tag):
        if ip not in self.wifi_devices:
            return
        self.wifi_devices[ip]["status"] = status
        item_id = self.wifi_devices[ip]["item_id"]
        name    = self.wifi_devices[ip]["name"]
        self.tree.item(item_id, values=(ip, name, status), tags=(tag,) if tag else ())

    def _on_close(self):
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = QuestUploader(root)
    root.mainloop()
