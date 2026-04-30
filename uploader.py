#!/usr/bin/env python3
"""Quest Mass Uploader — push video files to all Quest headsets simultaneously over WiFi."""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import subprocess
import threading
import socket
import ipaddress
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


class QuestUploader:
    def __init__(self, root):
        self.root = root
        self.root.title("Quest Mass Uploader")
        self.root.geometry("980x780")
        self.root.minsize(750, 640)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.adb_path = self._find_adb()

        # WiFi tab state
        self.wifi_devices = {}      # ip -> {model, status, item_id}
        self.file_paths = []
        self.scanning = False
        self.uploading = False

        # USB tab state
        self.usb_devices = {}       # serial -> {model, state, status, item_id}
        self.usb_monitoring = False
        self.auto_enable_var = tk.BooleanVar(value=True)

        self._setup_ui()
        self._check_adb()
        self._start_usb_monitor()

    # ------------------------------------------------------------------
    # ADB detection
    # ------------------------------------------------------------------

    def _find_adb(self):
        script_dir = Path(__file__).parent
        username = os.environ.get("USERNAME", os.environ.get("USER", ""))
        candidates = [
            str(script_dir / "ADB" / "adb.exe"),
            "adb",
            rf"C:\Users\{username}\AppData\Local\Android\Sdk\platform-tools\adb.exe",
            r"C:\Program Files\Android\android-sdk\platform-tools\adb.exe",
            r"C:\Program Files (x86)\Android\android-sdk\platform-tools\adb.exe",
        ]
        for c in candidates:
            try:
                r = subprocess.run([c, "version"], capture_output=True, timeout=5)
                if r.returncode == 0:
                    return c
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        return None

    def _check_adb(self):
        if self.adb_path:
            self.adb_label.config(text="ADB: Ready", foreground="green")
        else:
            self.adb_label.config(
                text="ADB not found — place the ADB folder next to this script",
                foreground="red"
            )

    # ------------------------------------------------------------------
    # UI root
    # ------------------------------------------------------------------

    def _setup_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        # Global ADB status bar
        top = ttk.Frame(self.root, padding=(10, 6))
        top.grid(row=0, column=0, sticky="ew")
        self.adb_label = ttk.Label(top, text="ADB: Checking...")
        self.adb_label.pack(side=tk.LEFT)

        # Notebook
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

        info = ttk.Label(
            parent,
            text=(
                "Plug headsets in via USB one batch at a time. "
                "Accept the 'Allow USB Debugging' prompt on each headset when it appears. "
                "Once enabled, the headset will be discoverable over WiFi — safe to unplug."
            ),
            wraplength=800, justify=tk.LEFT,
        )
        info.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        # Device list
        list_frame = ttk.LabelFrame(parent, text="USB Connected Devices", padding=5)
        list_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        cols = ("Serial", "Model", "Status")
        self.usb_tree = ttk.Treeview(list_frame, columns=cols, show="headings")
        self.usb_tree.heading("Serial", text="Serial / ID")
        self.usb_tree.heading("Model",  text="Device Model")
        self.usb_tree.heading("Status", text="Status")
        self.usb_tree.column("Serial", width=200, minwidth=150)
        self.usb_tree.column("Model",  width=200, minwidth=150)
        self.usb_tree.column("Status", width=420, minwidth=200)

        self.usb_tree.tag_configure("ready",    background="#d4edda", foreground="#155724")
        self.usb_tree.tag_configure("enabling", background="#fff3cd", foreground="#856404")
        self.usb_tree.tag_configure("unauth",   background="#fff3cd", foreground="#856404")
        self.usb_tree.tag_configure("error",    background="#f8d7da", foreground="#721c24")

        usb_vscroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.usb_tree.yview)
        self.usb_tree.configure(yscrollcommand=usb_vscroll.set)
        self.usb_tree.grid(row=0, column=0, sticky="nsew")
        usb_vscroll.grid(row=0, column=1, sticky="ns")

        # Controls row
        ctrl = ttk.Frame(parent)
        ctrl.grid(row=2, column=0, sticky="ew")
        ctrl.columnconfigure(0, weight=1)

        ttk.Checkbutton(
            ctrl,
            text="Automatically enable WiFi ADB when a headset is plugged in",
            variable=self.auto_enable_var,
        ).grid(row=0, column=0, sticky="w")

        btn_frame = ttk.Frame(ctrl)
        btn_frame.grid(row=0, column=1, sticky="e")
        ttk.Button(btn_frame, text="Enable All",
                   command=self._enable_all_usb, width=14).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_frame, text="Refresh",
                   command=self._refresh_usb, width=10).pack(side=tk.RIGHT)

        self.usb_status_label = ttk.Label(parent, text="Monitoring for USB connections...")
        self.usb_status_label.grid(row=3, column=0, sticky="w", pady=(6, 0))

    # --- USB monitor ---

    def _start_usb_monitor(self):
        self.usb_monitoring = True
        threading.Thread(target=self._usb_monitor_worker, daemon=True).start()

    def _usb_monitor_worker(self):
        prev = {}   # serial -> state string
        while self.usb_monitoring:
            try:
                if self.adb_path:
                    current = self._get_usb_devices_raw()

                    for serial, state in current.items():
                        if serial not in prev:
                            self.root.after(0, self._on_usb_appeared, serial, state)
                        elif prev[serial] != state and prev[serial] == "unauthorized" and state == "device":
                            # User just accepted the USB debugging dialog
                            self.root.after(0, self._on_usb_authorized, serial)

                    for serial in list(prev.keys()):
                        if serial not in current:
                            self.root.after(0, self._on_usb_removed, serial)

                    prev = current
            except Exception:
                pass
            time.sleep(2)

    def _get_usb_devices_raw(self):
        """Return {serial: state} for USB-connected devices only (no TCP/IP)."""
        r = subprocess.run(
            [self.adb_path, "devices"],
            capture_output=True, text=True, timeout=10,
        )
        result = {}
        for line in r.stdout.splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and ":" not in parts[0]:
                result[parts[0]] = parts[1]
        return result

    def _get_model_usb(self, serial):
        try:
            r = subprocess.run(
                [self.adb_path, "-s", serial, "shell", "getprop ro.product.model"],
                capture_output=True, text=True, timeout=10,
            )
            return r.stdout.strip() or "Unknown"
        except Exception:
            return "Unknown"

    def _on_usb_appeared(self, serial, state):
        if serial in self.usb_devices:
            return
        if state == "unauthorized":
            model = "—"
            status, tag = "Waiting — accept USB Debugging on the headset", "unauth"
        else:
            model = self._get_model_usb(serial)
            status, tag = "Connected", ""

        item_id = self.usb_tree.insert("", tk.END, values=(serial, model, status),
                                        tags=(tag,) if tag else ())
        self.usb_devices[serial] = {"model": model, "state": state,
                                     "status": status, "item_id": item_id}
        self.usb_status_label.config(text=f"{len(self.usb_devices)} device(s) connected via USB.")

        if state == "device" and self.auto_enable_var.get():
            threading.Thread(target=self._enable_wifi_adb, args=(serial,), daemon=True).start()

    def _on_usb_authorized(self, serial):
        """Called when a device transitions from unauthorized → device."""
        if serial not in self.usb_devices:
            return
        model = self._get_model_usb(serial)
        self.usb_devices[serial]["state"] = "device"
        self.usb_devices[serial]["model"] = model
        self._update_usb_row(serial, model, "Authorized — enabling WiFi ADB...", "enabling")
        if self.auto_enable_var.get():
            threading.Thread(target=self._enable_wifi_adb, args=(serial,), daemon=True).start()

    def _on_usb_removed(self, serial):
        if serial not in self.usb_devices:
            return
        item_id = self.usb_devices[serial]["item_id"]
        self.usb_tree.delete(item_id)
        del self.usb_devices[serial]
        self.usb_status_label.config(
            text=f"{len(self.usb_devices)} device(s) connected via USB."
            if self.usb_devices else "Monitoring for USB connections..."
        )

    def _enable_wifi_adb(self, serial):
        self.root.after(0, self._update_usb_row, serial,
                        self.usb_devices.get(serial, {}).get("model", "—"),
                        "Enabling WiFi ADB...", "enabling")
        try:
            r = subprocess.run(
                [self.adb_path, "-s", serial, "tcpip", "5555"],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0:
                self.root.after(0, self._update_usb_row, serial,
                                self.usb_devices.get(serial, {}).get("model", "—"),
                                "WiFi ADB enabled — safe to unplug", "ready")
            else:
                self.root.after(0, self._update_usb_row, serial,
                                self.usb_devices.get(serial, {}).get("model", "—"),
                                f"Failed: {(r.stderr or r.stdout).strip()}", "error")
        except Exception as e:
            self.root.after(0, self._update_usb_row, serial,
                            self.usb_devices.get(serial, {}).get("model", "—"),
                            f"Error: {e}", "error")

    def _update_usb_row(self, serial, model, status, tag):
        if serial not in self.usb_devices:
            return
        self.usb_devices[serial]["status"] = status
        item_id = self.usb_devices[serial]["item_id"]
        self.usb_tree.item(item_id, values=(serial, model, status),
                            tags=(tag,) if tag else ())

    def _refresh_usb(self):
        if not self.adb_path:
            return
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
                                "Make sure you accepted the USB Debugging prompt on each headset.")
            return
        for serial in targets:
            threading.Thread(target=self._enable_wifi_adb, args=(serial,), daemon=True).start()

    # ------------------------------------------------------------------
    # WiFi Upload tab
    # ------------------------------------------------------------------

    def _setup_wifi_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        # Top scan bar
        top = ttk.Frame(parent, padding=(10, 8))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        self.device_count_label = ttk.Label(top, text="Devices found: 0")
        self.device_count_label.grid(row=0, column=1, sticky="e", padx=10)

        self.scan_btn = ttk.Button(top, text="Scan Network", command=self.start_scan, width=16)
        self.scan_btn.grid(row=0, column=2, sticky="e")

        # Device list
        list_frame = ttk.LabelFrame(parent, text="Connected Devices", padding=5)
        list_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        cols = ("IP Address", "Model", "Status")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("IP Address", text="IP Address")
        self.tree.heading("Model",      text="Device Model")
        self.tree.heading("Status",     text="Status")
        self.tree.column("IP Address", width=140, minwidth=120)
        self.tree.column("Model",      width=200, minwidth=140)
        self.tree.column("Status",     width=520, minwidth=200)

        self.tree.tag_configure("done",      background="#d4edda", foreground="#155724")
        self.tree.tag_configure("skipped",   background="#e2e3e5", foreground="#383d41")
        self.tree.tag_configure("uploading", background="#fff3cd", foreground="#856404")
        self.tree.tag_configure("checking",  background="#d1ecf1", foreground="#0c5460")
        self.tree.tag_configure("error",     background="#f8d7da", foreground="#721c24")

        self.tree.bind("<<TreeviewSelect>>", self._on_selection_change)

        vscroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vscroll.grid(row=0, column=1, sticky="ns")

        # Selection buttons
        sel_bar = ttk.Frame(list_frame)
        sel_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Button(sel_bar, text="Select All",   command=self._select_all,   width=14).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(sel_bar, text="Deselect All", command=self._deselect_all, width=14).pack(side=tk.LEFT)
        self.selected_label = ttk.Label(sel_bar, text="Selected: 0 / 0")
        self.selected_label.pack(side=tk.RIGHT)

        # Settings
        settings = ttk.Frame(parent, padding=(10, 4))
        settings.grid(row=2, column=0, sticky="ew")
        settings.columnconfigure(0, weight=1)

        # File list
        files_frame = ttk.LabelFrame(settings, text="Video Files to Upload", padding=5)
        files_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        files_frame.columnconfigure(0, weight=1)

        self.file_listbox = tk.Listbox(files_frame, height=4, selectmode=tk.EXTENDED,
                                        activestyle="none")
        hscroll = ttk.Scrollbar(files_frame, orient=tk.HORIZONTAL,
                                  command=self.file_listbox.xview)
        self.file_listbox.configure(xscrollcommand=hscroll.set)
        self.file_listbox.grid(row=0, column=0, sticky="ew")
        hscroll.grid(row=1, column=0, sticky="ew")

        file_btns = ttk.Frame(files_frame)
        file_btns.grid(row=0, column=1, sticky="n", padx=(6, 0))
        ttk.Button(file_btns, text="Add Files",  command=self._add_files,              width=12).pack(pady=2)
        ttk.Button(file_btns, text="Remove",     command=self._remove_selected_files,  width=12).pack(pady=2)
        ttk.Button(file_btns, text="Clear All",  command=self._clear_files,            width=12).pack(pady=2)

        # Dest + concurrency
        opt_row = ttk.Frame(settings)
        opt_row.grid(row=1, column=0, sticky="ew")
        opt_row.columnconfigure(1, weight=1)

        ttk.Label(opt_row, text="Dest Folder:", width=14, anchor="w").grid(
            row=0, column=0, sticky="w", pady=3)
        self.dest_var = tk.StringVar(value="/sdcard/Showtime VR/Videos/3D")
        ttk.Entry(opt_row, textvariable=self.dest_var).grid(
            row=0, column=1, sticky="ew", padx=5)
        self.discover_btn = ttk.Button(opt_row, text="Discover",
                                        command=self._discover_path, width=10)
        self.discover_btn.grid(row=0, column=2, sticky="e")

        ttk.Label(opt_row, text="Concurrent:", width=14, anchor="w").grid(
            row=1, column=0, sticky="w", pady=3)
        batch_row = ttk.Frame(opt_row)
        batch_row.grid(row=1, column=1, sticky="w", padx=5)
        self.batch_var = tk.IntVar(value=30)
        ttk.Spinbox(batch_row, from_=1, to=300, textvariable=self.batch_var, width=8).pack(side=tk.LEFT)
        ttk.Label(batch_row, text="simultaneous uploads").pack(side=tk.LEFT, padx=6)

        # Bottom bar
        bottom = ttk.Frame(parent, padding=(10, 6))
        bottom.grid(row=3, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)

        self.progress_var = tk.DoubleVar()
        ttk.Progressbar(bottom, variable=self.progress_var, maximum=100).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6)
        )

        self.status_label = ttk.Label(bottom, text="Ready — scan the network to find devices.")
        self.status_label.grid(row=1, column=0, sticky="w")

        self.upload_btn = ttk.Button(
            bottom, text="Upload to Selected Devices",
            command=self.start_upload, state=tk.DISABLED, width=24,
        )
        self.upload_btn.grid(row=1, column=1, sticky="e")

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
        self._refresh_upload_btn()

    def _remove_selected_files(self):
        for i in reversed(self.file_listbox.curselection()):
            self.file_listbox.delete(i)
            self.file_paths.pop(i)
        self._refresh_upload_btn()

    def _clear_files(self):
        self.file_listbox.delete(0, tk.END)
        self.file_paths.clear()
        self._refresh_upload_btn()

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
        self._refresh_upload_btn()

    def _get_selected_ips(self):
        item_to_ip = {d["item_id"]: ip for ip, d in self.wifi_devices.items()}
        return [item_to_ip[iid] for iid in self.tree.selection() if iid in item_to_ip]

    def _refresh_upload_btn(self):
        ready = (
            bool(self._get_selected_ips())
            and bool(self.file_paths)
            and not self.uploading
            and not self.scanning
        )
        self.upload_btn.config(state=tk.NORMAL if ready else tk.DISABLED)

    # ------------------------------------------------------------------
    # Path discovery
    # ------------------------------------------------------------------

    def _discover_path(self):
        ips = self._get_selected_ips() or list(self.wifi_devices.keys())
        if not ips:
            messagebox.showwarning("No Devices", "Scan the network first to find devices.")
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
                capture_output=True, text=True, timeout=15,
            )
            if r.stdout.strip() == "EXISTS":
                found = known
            else:
                r = subprocess.run(
                    [self.adb_path, "-s", serial, "shell",
                     "find /sdcard -maxdepth 4 -type d -iname 'showtime*' 2>/dev/null"],
                    capture_output=True, text=True, timeout=30,
                )
                candidates = [l.strip() for l in r.stdout.splitlines() if l.strip()]
                for c in candidates:
                    for sub in (f"{c}/Videos/3D", f"{c}/Videos", c):
                        r2 = subprocess.run(
                            [self.adb_path, "-s", serial, "shell",
                             f'test -d "{sub}" && echo EXISTS || echo NOT_FOUND'],
                            capture_output=True, text=True, timeout=10,
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
            self.root.after(0, lambda p=found: self.status_label.config(text=f"Path found and set: {p}"))
        else:
            self.root.after(0, lambda: messagebox.showwarning(
                "Not Found",
                "Could not locate a Showtime VR folder on the device.\n"
                "Enter the destination path manually.",
            ))
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
        prefix = local_ip.rsplit(".", 1)[0]
        return f"{prefix}.0/24"

    def start_scan(self):
        if not self.adb_path:
            messagebox.showerror("ADB Not Found", "ADB is not installed or not in PATH.")
            return
        if self.scanning or self.uploading:
            return
        self.scanning = True
        self.scan_btn.config(state=tk.DISABLED, text="Scanning...")
        self.upload_btn.config(state=tk.DISABLED)
        self.tree.delete(*self.tree.get_children())
        self.wifi_devices.clear()
        self.device_count_label.config(text="Devices found: 0")
        self.selected_label.config(text="Selected: 0 / 0")
        self.status_label.config(text="Scanning network for devices on port 5555...")
        self.progress_var.set(0)
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        try:
            subnet = self._get_local_subnet()
            hosts = list(ipaddress.IPv4Network(subnet, strict=False).hosts())

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
                        capture_output=True, text=True, timeout=10,
                    )
                    if "connected" not in r.stdout.lower():
                        return
                    model_r = subprocess.run(
                        [self.adb_path, "-s", f"{ip_str}:5555", "shell",
                         "getprop ro.product.model"],
                        capture_output=True, text=True, timeout=10,
                    )
                    model = model_r.stdout.strip() or "Unknown"
                    self.root.after(0, self._add_wifi_device, ip_str, model)
                except Exception:
                    pass

            with ThreadPoolExecutor(max_workers=100) as ex:
                ex.map(probe, hosts)
        finally:
            self.root.after(0, self._scan_done)

    def _add_wifi_device(self, ip, model):
        item_id = self.tree.insert("", tk.END, values=(ip, model, "Ready"))
        self.wifi_devices[ip] = {"model": model, "status": "Ready", "item_id": item_id}
        self.tree.selection_add(item_id)
        total = len(self.tree.get_children())
        selected = len(self.tree.selection())
        self.device_count_label.config(text=f"Devices found: {total}")
        self.selected_label.config(text=f"Selected: {selected} / {total}")

    def _scan_done(self):
        self.scanning = False
        count = len(self.wifi_devices)
        self.scan_btn.config(state=tk.NORMAL, text="Scan Network")
        self.status_label.config(text=f"Scan complete — {count} device(s) found.")
        self._refresh_upload_btn()

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def start_upload(self):
        selected_ips = self._get_selected_ips()
        if not selected_ips:
            messagebox.showerror("No Devices Selected", "Select at least one device from the list.")
            return
        if not self.file_paths:
            messagebox.showerror("No Files", "Please add at least one video file.")
            return
        self.uploading = True
        self.upload_btn.config(state=tk.DISABLED)
        self.scan_btn.config(state=tk.DISABLED)
        for ip in selected_ips:
            self._set_wifi_device_status(ip, "Waiting...", "")
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
                capture_output=True, text=True, timeout=20,
            )
            out = r.stdout.strip()
            if out == "NOT_FOUND":
                return False
            return int(out) == local_size
        except Exception:
            return False

    def _upload_one(self, ip, file_paths, dest_dir, file_sizes, counter, total, lock):
        n = len(file_paths)
        uploaded = skipped = errors = 0

        for i, file_path in enumerate(file_paths):
            filename = Path(file_path).name
            remote_file = dest_dir.rstrip("/") + "/" + filename
            prefix = f"[{i+1}/{n}] {filename}: " if n > 1 else f"{filename}: "

            self.root.after(0, self._set_wifi_device_status, ip,
                            f"{prefix}Checking...", "checking")

            if self._file_exists_on_device(ip, remote_file, file_sizes[i]):
                skipped += 1
                self.root.after(0, self._set_wifi_device_status, ip,
                                f"{prefix}Already on device — skipped", "skipped")
                continue

            self.root.after(0, self._set_wifi_device_status, ip,
                            f"{prefix}Starting upload...", "uploading")
            try:
                proc = subprocess.Popen(
                    [self.adb_path, "-s", f"{ip}:5555", "push", file_path, dest_dir],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                )
                for line in proc.stdout:
                    line = line.strip()
                    if line.startswith("[") and "%" in line:
                        try:
                            pct = int(line.split("%")[0].strip("[").strip())
                            self.root.after(0, self._set_wifi_device_status, ip,
                                            f"{prefix}Uploading {pct}%", "uploading")
                        except ValueError:
                            pass
                proc.wait()
                if proc.returncode == 0:
                    uploaded += 1
                    self.root.after(0, self._set_wifi_device_status, ip, f"{prefix}Done", "done")
                else:
                    errors += 1
                    self.root.after(0, self._set_wifi_device_status, ip,
                                    f"{prefix}Transfer failed", "error")
            except Exception as e:
                errors += 1
                self.root.after(0, self._set_wifi_device_status, ip,
                                f"{prefix}Error — {e}", "error")

        parts = []
        if uploaded: parts.append(f"{uploaded} uploaded")
        if skipped:  parts.append(f"{skipped} already on device")
        if errors:   parts.append(f"{errors} failed")
        final_status = " | ".join(parts) if parts else "Done"
        final_tag = "error" if errors else ("skipped" if uploaded == 0 else "done")
        self.root.after(0, self._set_wifi_device_status, ip, final_status, final_tag)

        with lock:
            counter[0] += 1
            done = counter[0]
            pct = (done / total) * 100
        self.root.after(0, self.progress_var.set, pct)
        self.root.after(0, lambda d=done: self.status_label.config(
            text=f"Progress: {d}/{total} devices processed"
        ))

    def _upload_worker(self, selected_ips, file_paths):
        dest_dir = self.dest_var.get().strip()
        batch_size = self.batch_var.get()
        total = len(selected_ips)
        file_sizes = [os.path.getsize(p) for p in file_paths]
        counter = [0]
        lock = threading.Lock()

        with ThreadPoolExecutor(max_workers=batch_size) as ex:
            for ip in selected_ips:
                ex.submit(self._upload_one, ip, file_paths, dest_dir,
                          file_sizes, counter, total, lock)

        self.root.after(0, self._upload_done)

    def _upload_done(self):
        self.uploading = False
        self.scan_btn.config(state=tk.NORMAL)
        self._refresh_upload_btn()
        statuses = [d["status"] for d in self.wifi_devices.values()]
        all_uploaded = sum(1 for s in statuses if "uploaded" in s or s == "Done")
        all_skipped  = sum(1 for s in statuses if "already on device" in s and "uploaded" not in s)
        all_errors   = sum(1 for s in statuses if "failed" in s or "Error" in s)
        summary = (
            f"Finished — {all_uploaded} device(s) uploaded new files, "
            f"{all_skipped} fully skipped, {all_errors} had errors."
        )
        self.status_label.config(text=summary)
        messagebox.showinfo("Upload Complete", summary)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_wifi_device_status(self, ip, status, tag):
        if ip not in self.wifi_devices:
            return
        self.wifi_devices[ip]["status"] = status
        item_id = self.wifi_devices[ip]["item_id"]
        model = self.wifi_devices[ip]["model"]
        self.tree.item(item_id, values=(ip, model, status), tags=(tag,) if tag else ())

    def _on_close(self):
        self.usb_monitoring = False
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = QuestUploader(root)
    root.mainloop()
