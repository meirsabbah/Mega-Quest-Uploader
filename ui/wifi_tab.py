"""WiFi Upload tab — scan, upload, delete, and install across all devices."""

import ipaddress
import os
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core import adb
from core.utils import resource_dir
from ui.file_browser import open_file_browser


class WifiTab:
    def __init__(self, parent, app):
        self.app = app              # exposes app.root, app.adb_path

        self.wifi_devices = {}      # ip -> {name, status, item_id}
        self.file_paths = []
        self.busy = False
        self.scanning = False
        self._apk_paths = {}

        self.dest_var     = tk.StringVar(value="/sdcard/Showtime VR/Videos/3D")
        self.delete_var   = tk.StringVar()
        self.apk_var      = tk.StringVar()
        self.batch_var    = tk.IntVar(value=30)
        self.progress_var = tk.DoubleVar()
        self._device_progress = {}  # ip -> 0-100, tracks smooth per-device progress

        self._build(parent)

    @property
    def root(self):
        return self.app.root

    @property
    def adb_path(self):
        return self.app.adb_path

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=0)
        parent.rowconfigure(0, weight=1)

        # ── Left: device list ─────────────────────────────────────────
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
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", selectmode="extended")
        self._sort_col = None
        self._sort_asc = True
        for col in cols:
            self.tree.heading(col, text=col,
                              command=lambda c=col: self._sort_by(c))
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
        ttk.Button(sel_bar, text="Select All",   command=self._select_all,   width=13).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(sel_bar, text="Deselect All", command=self._deselect_all, width=13).pack(side=tk.LEFT)
        self.selected_label = ttk.Label(sel_bar, text="Selected: 0 / 0", foreground="#555")
        self.selected_label.pack(side=tk.RIGHT)

        prog_frame = ttk.Frame(left)
        prog_frame.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        prog_frame.columnconfigure(0, weight=1)
        ttk.Progressbar(prog_frame, variable=self.progress_var, maximum=100).grid(
            row=0, column=0, sticky="ew", pady=(0, 4))
        self.status_label = ttk.Label(
            prog_frame, text="Ready — scan the network to find devices.", foreground="#555")
        self.status_label.grid(row=1, column=0, sticky="w")

        # ── Right: operations ─────────────────────────────────────────
        right = ttk.Frame(parent, padding=(4, 8, 8, 8))
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        r = 0

        files_frame = ttk.LabelFrame(right, text="Files to Upload", padding=6)
        files_frame.grid(row=r, column=0, sticky="ew"); r += 1
        files_frame.columnconfigure(0, weight=1)
        self.file_listbox = tk.Listbox(
            files_frame, height=3, selectmode=tk.EXTENDED, activestyle="none",
            background="#2d2d2d", foreground="#ffffff",
            selectbackground="#0078d4", selectforeground="#ffffff",
            relief="flat", highlightthickness=1,
            highlightbackground="#3d3d3d", highlightcolor="#0078d4",
        )
        hsc = ttk.Scrollbar(files_frame, orient=tk.HORIZONTAL, command=self.file_listbox.xview)
        self.file_listbox.configure(xscrollcommand=hsc.set)
        self.file_listbox.grid(row=0, column=0, sticky="ew")
        hsc.grid(row=1, column=0, sticky="ew")
        fb = ttk.Frame(files_frame)
        fb.grid(row=0, column=1, sticky="n", padx=(4, 0))
        ttk.Button(fb, text="Add",    command=self._add_files,            width=8).pack(pady=1)
        ttk.Button(fb, text="Remove", command=self._remove_selected_files, width=8).pack(pady=1)
        ttk.Button(fb, text="Clear",  command=self._clear_files,           width=8).pack(pady=1)

        self.upload_btn = ttk.Button(right, text="Upload to Selected Devices",
                                     command=self.start_upload,
                                     state=tk.DISABLED, style="Accent.TButton")
        self.upload_btn.grid(row=r, column=0, sticky="ew", pady=(6, 10)); r += 1

        ttk.Separator(right, orient=tk.HORIZONTAL).grid(row=r, column=0, sticky="ew", pady=(0, 8)); r += 1

        del_frame = ttk.LabelFrame(right, text="Delete File from Devices", padding=6)
        del_frame.grid(row=r, column=0, sticky="ew"); r += 1
        del_frame.columnconfigure(0, weight=1)
        ttk.Entry(del_frame, textvariable=self.delete_var).grid(
            row=0, column=0, sticky="ew", pady=(0, 4))
        self.delete_btn = ttk.Button(del_frame, text="Delete from Selected Devices",
                                     command=self._delete_from_devices)
        self.delete_btn.grid(row=1, column=0, sticky="ew")

        ttk.Separator(right, orient=tk.HORIZONTAL).grid(row=r, column=0, sticky="ew", pady=8); r += 1

        apk_frame = ttk.LabelFrame(right, text="APK Installation", padding=6)
        apk_frame.grid(row=r, column=0, sticky="ew"); r += 1
        apk_frame.columnconfigure(0, weight=1)
        apk_top = ttk.Frame(apk_frame)
        apk_top.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        apk_top.columnconfigure(0, weight=1)
        self.apk_combo = ttk.Combobox(apk_top, textvariable=self.apk_var, state="readonly")
        self.apk_combo.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(apk_top, text="Refresh", command=self._scan_apk_folder, width=8).grid(row=0, column=1, padx=(0, 2))
        ttk.Button(apk_top, text="Browse",  command=self._browse_apk,      width=8).grid(row=0, column=2)
        self.install_btn = ttk.Button(apk_frame, text="Install on Selected Devices",
                                      command=self._install_apk)
        self.install_btn.grid(row=1, column=0, sticky="ew")

        ttk.Separator(right, orient=tk.HORIZONTAL).grid(row=r, column=0, sticky="ew", pady=8); r += 1

        opt_frame = ttk.LabelFrame(right, text="Options", padding=6)
        opt_frame.grid(row=r, column=0, sticky="ew"); r += 1
        opt_frame.columnconfigure(1, weight=1)
        ttk.Label(opt_frame, text="Destination:").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(opt_frame, textvariable=self.dest_var).grid(
            row=0, column=1, sticky="ew", padx=(4, 4), pady=(0, 4))
        self.discover_btn = ttk.Button(opt_frame, text="Discover",
                                       command=self._discover_path, width=9)
        self.discover_btn.grid(row=0, column=2, pady=(0, 4))
        ttk.Label(opt_frame, text="Concurrent:").grid(row=1, column=0, sticky="w")
        b_row = ttk.Frame(opt_frame)
        b_row.grid(row=1, column=1, columnspan=2, sticky="w", padx=4)
        ttk.Spinbox(b_row, from_=1, to=300, textvariable=self.batch_var, width=5).pack(side=tk.LEFT)
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
        adb_dir = resource_dir() / "ADB"
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
        return self._apk_paths.get(self.apk_var.get().strip())

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
            self._set_status(ip, "Waiting to install...", "")
        self.progress_var.set(0)
        threading.Thread(target=self._install_worker, args=(selected_ips, apk_path),
                         daemon=True).start()

    def _install_one(self, ip, apk_path, counter, total, lock):
        apk_name = Path(apk_path).name
        self.root.after(0, self._set_status, ip, f"Installing {apk_name}...", "uploading")
        success, msg = adb.install_apk_on_device(self.adb_path, ip, apk_path)
        if success:
            self.root.after(0, self._set_status, ip, f"Installed: {apk_name}", "done")
        else:
            self.root.after(0, self._set_status, ip, f"Failed: {msg}", "error")
        self._tick_progress(counter, total, lock, "processed")

    def _install_worker(self, selected_ips, apk_path):
        counter, lock = [0], threading.Lock()
        with ThreadPoolExecutor(max_workers=self.batch_var.get()) as ex:
            for ip in selected_ips:
                ex.submit(self._install_one, ip, apk_path, counter, len(selected_ips), lock)
        self.root.after(0, self._operation_done, "Install")

    # ------------------------------------------------------------------
    # Device selection
    # ------------------------------------------------------------------

    def _sort_by(self, col):
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True
        items = [(self.tree.set(iid, col), iid) for iid in self.tree.get_children()]
        items.sort(key=lambda x: x[0].lower(), reverse=not self._sort_asc)
        for rank, (_, iid) in enumerate(items):
            self.tree.move(iid, "", rank)
        arrow = " ▲" if self._sort_asc else " ▼"
        for c in ("IP Address", "Device Name", "Status"):
            self.tree.heading(c, text=c + (arrow if c == col else ""),
                              command=lambda cc=c: self._sort_by(cc))

    def _select_all(self):
        self.tree.selection_set(self.tree.get_children())
        self._on_selection_change()

    def _deselect_all(self):
        self.tree.selection_remove(self.tree.get_children())
        self._on_selection_change()

    def _on_selection_change(self, *_):
        selected = len(self.tree.selection())
        total    = len(self.tree.get_children())
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
        has_apk = bool(self._apk_paths and self.apk_var.get())
        self.install_btn.config(state=tk.NORMAL if (has_devices and has_apk and idle) else tk.DISABLED)

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
        found = adb.discover_showtime_path(self.adb_path, ip)
        if found:
            self.root.after(0, self.dest_var.set, found)
            self.root.after(0, lambda p=found: self.status_label.config(text=f"Path found: {p}"))
        else:
            self.root.after(0, lambda: messagebox.showwarning(
                "Not Found", "Could not locate a Showtime VR folder.\nEnter the path manually."))
            self.root.after(0, lambda: self.status_label.config(text="Path not found — enter manually."))
        self.root.after(0, lambda: self.discover_btn.config(state=tk.NORMAL, text="Discover"))

    # ------------------------------------------------------------------
    # Network scan
    # ------------------------------------------------------------------

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
            subnet = adb.get_local_subnet()
            hosts = list(ipaddress.IPv4Network(subnet, strict=False).hosts())
            self.root.after(0, self.status_label.config,
                            {"text": f"Scanning {subnet} ({len(hosts)} addresses)..."})

            def probe(ip):
                result = adb.probe_device(self.adb_path, str(ip))
                if result:
                    _, name = result
                    self.root.after(0, self._add_device, str(ip), name)

            with ThreadPoolExecutor(max_workers=100) as ex:
                ex.map(probe, hosts)
        finally:
            self.root.after(0, self._scan_done)

    def _add_device(self, ip, name):
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

    @staticmethod
    def _make_bar(pct, width=10):
        filled = round(pct / 100 * width)
        return "█" * filled + "░" * (width - filled)

    def _update_overall_bar(self):
        if not self._device_progress:
            return
        avg = sum(self._device_progress.values()) / len(self._device_progress)
        self.progress_var.set(avg)

    def start_upload(self):
        selected_ips = self._get_selected_ips()
        if not selected_ips or not self.file_paths:
            return
        self.busy = True
        self.scan_btn.config(state=tk.DISABLED)
        self._refresh_buttons()
        for ip in selected_ips:
            self._set_status(ip, "Waiting...", "")
        self._device_progress = {ip: 0.0 for ip in selected_ips}
        self.progress_var.set(0)
        threading.Thread(
            target=self._upload_worker,
            args=(selected_ips, list(self.file_paths)),
            daemon=True,
        ).start()

    def _upload_one(self, ip, file_paths, dest_dir, file_sizes, counter, total, lock):
        n = len(file_paths)
        uploaded = skipped = errors = 0
        last_error = ""

        for i, file_path in enumerate(file_paths):
            filename    = Path(file_path).name
            remote_file = dest_dir.rstrip("/") + "/" + filename
            prefix      = f"[{i+1}/{n}] {filename}: " if n > 1 else f"{filename}: "

            self.root.after(0, self._set_status, ip, f"{prefix}Checking...", "checking")
            if adb.file_exists_on_device(self.adb_path, ip, remote_file, file_sizes[i]):
                skipped += 1
                self.root.after(0, self._set_status, ip, f"{prefix}Already on device — skipped", "skipped")
                continue

            self.root.after(0, self._set_status, ip, f"{prefix}Starting upload...", "uploading")

            def _progress(pct, _ip=ip, _prefix=prefix, _i=i, _n=n):
                device_pct = (_i * 100 + pct) / _n
                self._device_progress[_ip] = device_pct
                bar = WifiTab._make_bar(pct)
                self.root.after(0, self._set_status, _ip, f"{_prefix}[{bar}] {pct}%", "uploading")
                self.root.after(0, self._update_overall_bar)

            success, error = adb.push_file(self.adb_path, ip, file_path, dest_dir, _progress)
            if success:
                uploaded += 1
                self.root.after(0, self._set_status, ip, f"{prefix}Done", "done")
            else:
                errors += 1
                last_error = error
                self.root.after(0, self._set_status, ip, f"{prefix}Transfer failed ({error})", "error")

        parts = []
        if uploaded: parts.append(f"{uploaded} uploaded")
        if skipped:  parts.append(f"{skipped} already on device")
        if errors:
            parts.append(f"{errors} failed ({last_error})" if last_error else f"{errors} failed")
        final = " | ".join(parts) if parts else "Done"
        self.root.after(0, self._set_status, ip, final,
                        "error" if errors else ("skipped" if not uploaded else "done"))
        self._device_progress[ip] = 100.0
        self.root.after(0, self._update_overall_bar)
        self._tick_progress(counter, total, lock, "uploaded")

    def _upload_worker(self, selected_ips, file_paths):
        dest_dir   = self.dest_var.get().strip()
        file_sizes = [os.path.getsize(p) for p in file_paths]
        counter, lock = [0], threading.Lock()
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
            self._set_status(ip, "Waiting to delete...", "")
        self.progress_var.set(0)
        dest_dir    = self.dest_var.get().strip()
        remote_file = dest_dir.rstrip("/") + "/" + filename
        threading.Thread(
            target=self._delete_worker,
            args=(selected_ips, remote_file, filename),
            daemon=True,
        ).start()

    def _delete_one(self, ip, remote_file, filename, counter, total, lock):
        self.root.after(0, self._set_status, ip, f"Checking for {filename}...", "checking")
        result, _ = adb.delete_file_from_device(self.adb_path, ip, remote_file)
        if result == "not_found":
            self.root.after(0, self._set_status, ip, "File not found — skipped", "skipped")
        elif result == "deleted":
            self.root.after(0, self._set_status, ip, f"Deleted: {filename}", "done")
        else:
            self.root.after(0, self._set_status, ip, "Delete failed", "error")
        self._tick_progress(counter, total, lock, "deleted")

    def _delete_worker(self, selected_ips, remote_file, filename):
        counter, lock = [0], threading.Lock()
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
            text=f"Progress: {d}/{total} devices {verb}"))

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
        else:
            ok  = sum(1 for s in statuses if s.startswith("Installed"))
            err = sum(1 for s in statuses if "Failed" in s or "Error" in s)
            summary = f"Install complete — {ok} succeeded, {err} failed."
        self.status_label.config(text=summary)
        messagebox.showinfo(f"{op_name} Complete", summary)

    def _set_status(self, ip, status, tag):
        if ip not in self.wifi_devices:
            return
        self.wifi_devices[ip]["status"] = status
        item_id = self.wifi_devices[ip]["item_id"]
        name    = self.wifi_devices[ip]["name"]
        self.tree.item(item_id, values=(ip, name, status), tags=(tag,) if tag else ())

    # ------------------------------------------------------------------
    # File browser
    # ------------------------------------------------------------------

    def _on_device_double_click(self, event):
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return
        item_to_ip = {d["item_id"]: ip for ip, d in self.wifi_devices.items()}
        ip = item_to_ip.get(item_id)
        if not ip:
            return

        def deselect_cb():
            self.tree.selection_remove(self.wifi_devices[ip]["item_id"])
            self._on_selection_change()

        open_file_browser(
            self.root, self.adb_path,
            ip, self.wifi_devices[ip]["name"],
            self.dest_var.get().strip(),
            self.delete_var,
            deselect_cb,
        )
