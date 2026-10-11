"""Remove RiftUnpack's per-user Windows Explorer entries."""

import sys

if sys.platform != "win32":
    raise SystemExit("This uninstaller only runs on Windows.")
import winreg

for key_path in (
    r"Software\Classes\*\shell\RiftUnpack",
    r"Software\Classes\Directory\shell\RiftUnpack",
):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path + r"\command", 0, winreg.KEY_WRITE):
            pass
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path + r"\command")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
    except FileNotFoundError:
        pass
print("RiftUnpack context-menu entries removed.")
