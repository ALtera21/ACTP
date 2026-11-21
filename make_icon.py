"""Make ACTP's logo files from your own picture or emoji.

Run it from the project folder with uv (Pillow is borrowed for this one run):

    uv run --with pillow python make_icon.py mylogo.png
    uv run --with pillow python make_icon.py "📈"

It writes src/actp/actp_icon.png (the window logo) and src/actp/actp_icon.ico
(the .exe logo). Then rebuild the app so the .exe picks the new logo up:

    uv run pyinstaller --noconsole --onedir --name ACTP --clean ^
        --collect-all MetaTrader5 --collect-all numpy ^
        --hidden-import MetaTrader5 --icon src\\actp\\actp_icon.ico ^
        --add-data "src\\actp\\actp_icon.png;actp" run_actp.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))


def main() -> None:
    from PIL import Image

    from actp.logo import render_text

    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    source = sys.argv[1]
    if not Path(source).is_file() and Path(source).suffix:
        raise SystemExit(f"File not found: {source}")
    if Path(source).is_file():
        picture = Image.open(source).convert("RGBA")
        side = max(picture.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(picture, ((side - picture.size[0]) // 2,
                               (side - picture.size[1]) // 2))
        picture = square.resize((256, 256), Image.LANCZOS)
    else:
        picture = render_text(source, 256)
    out = HERE / "src" / "actp"
    picture.save(out / "actp_icon.png")
    picture.save(out / "actp_icon.ico",
                 sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                        (64, 64), (128, 128), (256, 256)])
    print(f"Wrote {out / 'actp_icon.png'} and {out / 'actp_icon.ico'}")


if __name__ == "__main__":
    main()
