"""Single-device file browser dialog."""

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from core import adb


def open_file_browser(root, adb_path, ip, device_name, dest_dir, delete_var, deselect_cb=None):
    """
    Open a modal dialog listing files in dest_dir on the device.
    delete_var  — tk.StringVar on the main window's Delete field (pre-filled on Copy Name).
    deselect_cb — optional callable; invoked by the 'Deselect Device' button.
    """
    dlg = tk.Toplevel(root)
    dlg.title(f"Files on {device_name}")
    dlg.geometry("640x420")
    dlg.transient(root)
    dlg.grab_set()
    dlg.columnconfigure(0, weight=1)
    dlg.rowconfigure(1, weight=1)

    ttk.Label(dlg, text=dest_dir, foreground="gray").grid(
        row=0, column=0, sticky="ew", padx=10, pady=(8, 2))

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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _populate(files, status_msg=None):
        ftree.delete(*ftree.get_children())
        for filename, size in files:
            ftree.insert("", tk.END, values=(filename, size))
        status_lbl.config(text=status_msg if status_msg else
                          f"{len(files)} file(s)  —  double-click a file to delete it")

    def load_files():
        try:
            files = adb.list_device_files(adb_path, ip, dest_dir)
            dlg.after(0, lambda f=files: _populate(f) if f else _populate([], "Folder is empty"))
        except RuntimeError as e:
            dlg.after(0, lambda m=str(e): _populate([], m))
        except Exception as e:
            dlg.after(0, lambda m=str(e): status_lbl.config(text=f"Error: {m}"))

    def refresh():
        status_lbl.config(text="Loading...")
        threading.Thread(target=load_files, daemon=True).start()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def delete_selected():
        selected = ftree.selection()
        if not selected:
            return
        names = [ftree.item(i, "values")[0] for i in selected]
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Delete {len(names)} file(s) from {device_name}?",
            parent=dlg,
        ):
            return
        status_lbl.config(text="Deleting...")

        def do_delete(sel=selected, fns=names):
            removed = []
            for item_id, fn in zip(sel, fns):
                remote = dest_dir.rstrip("/") + "/" + fn
                result, _ = adb.delete_file_from_device(adb_path, ip, remote)
                if result == "deleted":
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

    def copy_filename():
        selected = ftree.selection()
        if not selected:
            return
        filename = ftree.item(selected[0], "values")[0]
        dlg.clipboard_clear()
        dlg.clipboard_append(filename)
        delete_var.set(filename)    # pre-fill Delete field on main window
        status_lbl.config(text=f"Copied: {filename}")

    def deselect_device():
        if deselect_cb:
            deselect_cb()
        dlg.destroy()

    # ------------------------------------------------------------------
    # Button bar
    # ------------------------------------------------------------------

    ftree.bind("<Double-1>", lambda e: delete_selected())

    ttk.Button(btn_bar, text="Refresh",         command=refresh,         width=10).pack(side=tk.LEFT)
    ttk.Button(btn_bar, text="Delete Selected", command=delete_selected, width=16).pack(side=tk.LEFT, padx=4)
    ttk.Button(btn_bar, text="Copy Name",       command=copy_filename,   width=12).pack(side=tk.LEFT, padx=4)
    ttk.Button(btn_bar, text="Deselect Device", command=deselect_device, width=16).pack(side=tk.LEFT)
    ttk.Button(btn_bar, text="Close",           command=dlg.destroy,     width=10).pack(side=tk.RIGHT)

    refresh()
