"""USB Setup tab — detects USB-connected headsets and enables WiFi ADB."""

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from core import adb


class UsbTab:
    def __init__(self, parent, app):
        self.app = app              # exposes app.root, app.adb_path
        self.usb_devices = {}       # serial -> {name, state, status, item_id}
        self.auto_enable_var = tk.BooleanVar(value=True)
        self._refreshing = False    # prevents overlapping poll threads
        self._build(parent)

    # Convenience properties so the rest of the class doesn't reach through app
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
        parent.rowconfigure(1, weight=1)

        ttk.Label(
            parent,
            text=(
                "Plug headsets in via USB. When the 'Allow USB Debugging' prompt appears, "
                "tap Allow and check 'Always allow from this computer' — this only needs to "
                "be done once per headset. WiFi ADB must be re-enabled once after each reboot: "
                "plug in via USB and click Enable All. "
                "Quest 3 tip: enable 'Wireless Debugging' in Developer Settings on the headset "
                "to skip USB entirely after reboots."
            ),
            wraplength=800, justify=tk.LEFT,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        lf = ttk.LabelFrame(parent, text="USB Connected Devices", padding=5)
        lf.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)

        cols = ("Serial", "Device Name", "Status")
        self.tree = ttk.Treeview(lf, columns=cols, show="headings")
        self.tree.heading("Serial",      text="Serial / ID")
        self.tree.heading("Device Name", text="Device Name")
        self.tree.heading("Status",      text="Status")
        self.tree.column("Serial",      width=200, minwidth=150)
        self.tree.column("Device Name", width=220, minwidth=150)
        self.tree.column("Status",      width=380, minwidth=200)
        self.tree.tag_configure("ready",    background="#1a3d2b", foreground="#00bc8c")
        self.tree.tag_configure("enabling", background="#3d2e00", foreground="#f39c12")
        self.tree.tag_configure("unauth",   background="#3d2e00", foreground="#f39c12")
        self.tree.tag_configure("error",    background="#3d1010", foreground="#e74c3c")

        vsc = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsc.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
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
        ttk.Button(bf, text="Enable All",     command=self._enable_all, width=14).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(bf, text="Detect Devices", command=self.refresh,     width=16).pack(side=tk.RIGHT)

        self.status_label = ttk.Label(parent, text="Monitoring for USB connections...")
        self.status_label.grid(row=3, column=0, sticky="w", pady=(6, 0))

    # ------------------------------------------------------------------
    # Device lifecycle callbacks (called from main thread via root.after)
    # ------------------------------------------------------------------

    def on_appeared(self, serial, state):
        if serial in self.usb_devices:
            return
        if state == "unauthorized":
            name = "—"
            status, tag = "Waiting — accept USB Debugging on the headset", "unauth"
        else:
            _, name = adb.get_device_info_usb(self.adb_path, serial)
            status, tag = "Connected", ""

        item_id = self.tree.insert("", tk.END, values=(serial, name, status),
                                   tags=(tag,) if tag else ())
        self.usb_devices[serial] = {"name": name, "state": state,
                                    "status": status, "item_id": item_id}
        self.status_label.config(text=f"{len(self.usb_devices)} device(s) connected via USB.")

        if state == "device" and self.auto_enable_var.get():
            threading.Thread(target=self._do_enable, args=(serial,), daemon=True).start()

    def on_authorized(self, serial):
        if serial not in self.usb_devices:
            return
        _, name = adb.get_device_info_usb(self.adb_path, serial)
        self.usb_devices[serial].update({"state": "device", "name": name})
        self._update_row(serial, name, "Authorized — enabling WiFi ADB...", "enabling")
        if self.auto_enable_var.get():
            threading.Thread(target=self._do_enable, args=(serial,), daemon=True).start()

    def on_removed(self, serial):
        if serial not in self.usb_devices:
            return
        self.tree.delete(self.usb_devices[serial]["item_id"])
        del self.usb_devices[serial]
        self.status_label.config(
            text=f"{len(self.usb_devices)} device(s) connected via USB."
            if self.usb_devices else "Monitoring for USB connections..."
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def refresh(self):
        if self.adb_path and not self._refreshing:
            threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        self._refreshing = True
        try:
            current = adb.get_usb_devices_raw(self.adb_path)
            for serial, state in current.items():
                if serial not in self.usb_devices:
                    self.root.after(0, self.on_appeared, serial, state)
                elif state == "device" and self.usb_devices[serial]["state"] == "unauthorized":
                    # Device just got authorized on the headset — enable without needing a restart
                    self.root.after(0, self.on_authorized, serial)
            for serial in list(self.usb_devices.keys()):
                if serial not in current:
                    self.root.after(0, self.on_removed, serial)
        finally:
            self._refreshing = False

def _enable_all(self):
        targets = [s for s, d in self.usb_devices.items() if d["state"] == "device"]
        if not targets:
            messagebox.showinfo(
                "Nothing to enable",
                "No authorized devices connected.\n"
                "Accept the USB Debugging prompt on each headset first.",
            )
            return
        for serial in targets:
            threading.Thread(target=self._do_enable, args=(serial,), daemon=True).start()

    def _do_enable(self, serial):
        name = self.usb_devices.get(serial, {}).get("name", "—")
        self.root.after(0, self._update_row, serial, name, "Enabling WiFi ADB...", "enabling")
        success, msg = adb.enable_wifi_adb(self.adb_path, serial)
        tag = "ready" if success else "error"
        self.root.after(0, self._update_row, serial, name, msg, tag)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_row(self, serial, name, status, tag):
        if serial not in self.usb_devices:
            return
        self.usb_devices[serial]["status"] = status
        self.tree.item(self.usb_devices[serial]["item_id"],
                       values=(serial, name, status), tags=(tag,) if tag else ())
