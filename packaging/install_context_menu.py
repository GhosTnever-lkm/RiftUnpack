"""Install a per-user Windows Explorer 'Extract here with RiftUnpack' command."""

import shutil
import sys
from pathlib import Path

if sys.platform != "win32":
    raise SystemExit("This installer only runs on Windows.")
import winreg

configured = sys.argv[1] if len(sys.argv) > 1 else shutil.which("riftunpack")
if not configured:
    raise SystemExit(
        "Pass the path to RiftUnpack.exe or install its CLI before running this script."
    )
exe = Path(configured).resolve()
command = f'"{exe}" extract "%1" --here'
for key_path, label in (
    (r"Software\Classes\*\shell\RiftUnpack", "Extract here with RiftUnpack"),
    (r"Software\Classes\Directory\shell\RiftUnpack", "Extract here with RiftUnpack"),
):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "MUIVerb", 0, winreg.REG_SZ, label)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path + r"\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
print("RiftUnpack context-menu entries installed for the current Windows user.")
