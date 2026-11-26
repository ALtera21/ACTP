"""The ACTP logo: the little picture in the window's title bar and taskbar.

=====  TO CHANGE THE LOGO, EDIT THE NEXT LINE  =====
Use ONE of these:
  * a picture file name:  "actp_icon.png"  (put the file in this same folder),
    or a full path such as  r"C:\\Pictures\\mylogo.png".
    PNG is best; a .ico file also works.
  * an emoji or a short text, e.g.  "\U0001F4C8"  or  "A"
    (needs the Pillow package once:  pip install pillow ).
Restart ACTP to see the change.

The picture of the .exe file itself (and its taskbar button when it is not
running) is NOT set from the code: Windows takes it from the .ico given when
the .exe is built. See make_icon.py in the project folder.
"""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path
from typing import Any

APP_ICON = "actp_icon.png"

APP_ID = "ACTP.AutoCopyTradePlan"
_HERE = Path(__file__).resolve().parent


def set_taskbar_identity() -> None:
    """Windows: show ACTP's own icon in the taskbar, not python.exe's."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:  # not Windows
        pass


def find_file(name: str) -> Path | None:
    """The picture file: a full path, or a name next to this file / in the
    packed .exe."""
    given = Path(name)
    if given.is_absolute():
        return given if given.is_file() else None
    roots = [_HERE, Path.cwd()]
    packed = getattr(sys, "_MEIPASS", None)
    if packed:
        roots += [Path(packed) / "actp", Path(packed)]
    for root in roots:
        if (root / name).is_file():
            return root / name
    return None


_EMOJI_FONTS = (
    ("seguiemj.ttf", 109),            # Windows
    ("NotoColorEmoji.ttf", 109),      # Linux
    ("/System/Library/Fonts/Apple Color Emoji.ttc", 160),  # macOS
)


def render_text(text: str, size: int = 256):
    """A square RGBA picture of an emoji / short text (needs Pillow)."""
    from PIL import Image, ImageDraw, ImageFont

    fonts = []
    for path, points in _EMOJI_FONTS:
        try:
            fonts.append(ImageFont.truetype(path, points))
        except OSError:
            continue
    fonts.append(ImageFont.load_default(size=int(size * 0.7)))
    box = None
    for font in fonts:  # the first font that really has these characters
        canvas = Image.new("RGBA", (size * 3, size * 3), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        try:
            draw.text((size // 2, size // 2), text, font=font,
                      embedded_color=True, fill="white")
        except (TypeError, ValueError, OSError):
            draw.text((size // 2, size // 2), text, font=font, fill="white")
        box = canvas.getbbox()
        if box is not None:
            break
    if box is None:
        raise ValueError(f"Could not draw {text!r} with any font.")
    glyph = canvas.crop(box)
    side = max(glyph.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(glyph, ((side - glyph.size[0]) // 2,
                         (side - glyph.size[1]) // 2))
    return square.resize((size, size), Image.LANCZOS)


def apply(root: Any) -> str | None:
    """Put the logo on the window (and every later window). Returns a
    warning text when the logo could not be used, else None; the window
    always still opens."""
    import tkinter as tk

    try:
        path = find_file(APP_ICON)
        if path is not None:
            if path.suffix.lower() == ".ico":
                root.iconbitmap(default=str(path))
                return None
            image = tk.PhotoImage(file=str(path))
        else:
            if len(APP_ICON) > 8 or "." in APP_ICON:
                return f"Logo file '{APP_ICON}' not found (see logo.py)."
            picture = render_text(APP_ICON, 64)
            buffer = io.BytesIO()
            picture.save(buffer, format="PNG")
            image = tk.PhotoImage(
                data=base64.b64encode(buffer.getvalue()).decode("ascii"))
        root.iconphoto(True, image)
        root._actp_logo = image  # keep a reference or Tk drops it
        return None
    except ImportError:
        return ("The emoji logo needs the Pillow package: pip install pillow "
                "(or use a picture file in logo.py).")
    except Exception as exc:  # a bad logo must never stop ACTP
        return f"Logo not used: {exc}"
