"""Path helpers — safe to import from any submodule."""

import sys
from pathlib import Path


def resource_dir():
    """Directory next to the exe — for user-placed files like the ADB folder."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(sys.argv[0]).resolve().parent


def bundle_dir():
    """PyInstaller temp extraction dir — for files bundled with --add-data."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(sys.argv[0]).resolve().parent
