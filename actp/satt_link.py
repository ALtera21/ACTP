"""Link from the SATT chart tool (MetaTrader 5) to ACTP.

SATT writes the plan text as a small file into a folder inside MetaTrader's
shared "Common\\Files" area; ACTP picks the file up and sends the plan through
the same checks as a pasted message. ACTP also writes a heartbeat file once a
second, so SATT can refuse to send while ACTP is not running (a plan must
never wait in the folder and be sent later, at a different price).

Pure file handling (no tkinter, no MetaTrader) so it can be tested.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Callable

FOLDER_NAME = "ACTP_inbox"
HEARTBEAT_FILE = "actp_alive.txt"
PLAN_SUFFIX = ".plan"
MAX_AGE_SECONDS = 30  # an older plan file is thrown away, never sent


@dataclass(frozen=True)
class Incoming:
    name: str
    text: str
    expired: bool = False


def decode(data: bytes) -> str:
    """Text written by MQL5 (UTF-16 with a BOM) or by anything else (UTF-8)."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    elif data and data.count(b"\x00") * 4 > len(data):  # UTF-16 without BOM
        text = data.decode("utf-16-le", errors="replace")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
    return text.replace("\r\n", "\n").replace("\x00", "").strip()


def display_path(path: Path | str) -> str:
    """The folder for the log without the Windows user name:
    C:\\Users\\<name>\\AppData\\Roaming\\MetaQuotes\\... is shown as
    AppData\\***\\MetaQuotes\\..."""
    parts = PureWindowsPath(str(path)).parts
    for index, part in enumerate(parts):
        if part.lower() == "appdata":
            rest = parts[index + 2:]  # skip the folder after AppData (Roaming)
            return "\\".join(("AppData", "***") + tuple(rest))
    return "\\".join(("...",) + tuple(parts[-5:]))  # no AppData: last folders only


class Inbox:
    def __init__(self, directory: Path | str,
                 now: Callable[[], float] = time.time,
                 max_age: float = MAX_AGE_SECONDS) -> None:
        self.directory = Path(directory)
        self._now = now
        self.max_age = max_age
        self.directory.mkdir(parents=True, exist_ok=True)

    def heartbeat(self) -> None:
        """Tell SATT that ACTP is running (the time, as a plain number)."""
        target = self.directory / HEARTBEAT_FILE
        temp = self.directory / (HEARTBEAT_FILE + ".tmp")
        try:
            temp.write_text(str(int(self._now())), encoding="ascii")
            os.replace(temp, target)
        except OSError:
            pass  # SATT is reading it right now: the next beat will do

    def poll(self) -> list[Incoming]:
        """Take every plan file out of the folder, oldest first. A file is
        removed as soon as it is read, so a plan is handed over only once."""
        found: list[Incoming] = []
        for path in sorted(self.directory.glob("*" + PLAN_SUFFIX)):
            try:
                age = self._now() - path.stat().st_mtime
                data = path.read_bytes()
                path.unlink()
            except OSError:
                continue  # still being written or locked: try next time
            text = decode(data)
            if not text:
                continue
            found.append(Incoming(path.name, text, expired=age > self.max_age))
        return found
