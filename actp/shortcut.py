"""Desktop shortcut for ACTP (Windows), offered on the very first start."""

from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

NAME = "ACTP"


def _q(text: str) -> str:
    """A PowerShell single-quoted string."""
    return "'" + str(text).replace("'", "''") + "'"


def build_script(target: str, arguments: str, workdir: str, icon: str,
                 name: str = NAME) -> str:
    """PowerShell that writes <Desktop>\\<name>.lnk (the real Desktop folder,
    also when it is moved to OneDrive)."""
    return (
        "$d = [Environment]::GetFolderPath('Desktop'); "
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut("
        f"(Join-Path $d {_q(name + '.lnk')})); "
        f"$s.TargetPath = {_q(target)}; "
        f"$s.Arguments = {_q(arguments)}; "
        f"$s.WorkingDirectory = {_q(workdir)}; "
        f"$s.IconLocation = {_q(icon + ',0')}; "
        f"$s.Description = {_q(name)}; "
        "$s.Save()"
    )


def encode(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def shortcut_parts() -> tuple[str, str, str, str]:
    """(target, arguments, working folder, icon) for how ACTP runs now."""
    if getattr(sys, "frozen", False):  # the built .exe
        exe = Path(sys.executable)
        return str(exe), "", str(exe.parent), str(exe)
    from actp import logo
    root = Path(__file__).resolve().parents[2]
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")  # no black console window
    target = windowless if windowless.is_file() else python
    script = root / "run_actp.py"
    arguments = f'"{script}"' if script.is_file() else "-m actp.app"
    icon = logo.find_file("actp_icon.ico")
    return str(target), arguments, str(root), str(icon or target)


def create_desktop_shortcut() -> str | None:
    """Make the shortcut. Returns None when done, else the reason it failed."""
    if sys.platform != "win32":
        return "Desktop shortcuts are only made on Windows."
    try:
        script = build_script(*shortcut_parts())
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-EncodedCommand", encode(script)],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if result.returncode != 0:
        return (result.stderr or result.stdout or "PowerShell failed").strip()
    return None
