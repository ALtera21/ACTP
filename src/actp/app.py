"""
ACTP - Auto Copy Trade Plan. LIVE account only.

Paste a trade plan from WhatsApp, read the summary, confirm. ACTP places the
layered orders on whichever MetaTrader 5 account is logged in. Test on a DEMO
account first.

Run from the project root:  uv run python -m actp.app
Settings (symbol, lot, auto cut loss, SCL) are in the Settings button.
While this window is open it also monitors Cut Loss (if enabled) and Auto-TP.
Closing the window stops that monitoring; orders keep their broker SL/TP.
"""
import logging
import random
import time
import subprocess
import sys
import tkinter as tk
import traceback
from datetime import datetime
from decimal import Decimal, InvalidOperation
from logging.handlers import TimedRotatingFileHandler
from tkinter import messagebox, scrolledtext, ttk
from typing import Any

from actp import config
from actp import settings as cfg
from actp.broker import BrokerError
from actp.chart_levels import ChartLevelsWriter
from actp import confirm as confirm_logic
from actp import i18n
from actp import logo
from actp import shortcut
from actp import theme
from actp.dispatcher import Dispatcher
from actp.execution import (
    ExecutionHalted, ExecutionManager, plan_id, position_in_profit)
from actp.monitor import CutLossMonitor
from actp.plan_store import JsonPlanStore
from actp.layers import (
    MAX_LAYERS, MIN_LAYERS, lots_for_weights, parse_layer_amount,
    resolve_weights, summarize_lots,
)
from actp.manual import SIDES, build_plan_text, collect, default_notes
from actp.parser import parse_message
from actp.timeframes import TIMEFRAME_SECONDS, TIMEFRAMES, clock_text

STATE_PATH = config.APP_DIR / "state" / "actp_state.json"
LOG_PATH = config.APP_DIR / "logs" / "actp.log"
# Must stay well below MAX_POLL_GAP_SECONDS (15) in config.py.
POLL_MS = 3000
RED = "#b00020"
DARK_BLUE = "#22535E"
GREEN = "#1b5e20"
P = theme.palette(config.THEME)
GREY = P["grey"]
GREEN_TEXT = P["green_text"]
RED_TEXT = P["red_text"]
DANGER_BG = P["danger_bg"]
BUY_GREEN = "#2e7d32"
SELL_RED = "#d30404"   # a lighter orange-red, so it can't be mistaken for KILL ALL (#b00020)
VERSION = "2.0.2"

RISK_TEXT = (
    "ACTP places REAL orders on whichever MetaTrader 5 account is logged in, "
    "and can lose money.\n\n"
    "- Try it on a DEMO account first.\n"
    "- It only understands one signal format (PLAN XAUUSDr ...).\n"
    "- It has no warranty. You are responsible for every order.\n\n"
    "Continue?"
)

class AccountNotSupported(Exception):
    """The logged-in account cannot run ACTP."""

def setup_file_log() -> logging.Logger:
    """One file per day (kept 30 days)."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("actp")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = TimedRotatingFileHandler(
            LOG_PATH, when="midnight", backupCount=30, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(handler)
    return logger


def fatal(message: str) -> None:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("ACTP", message)
    root.destroy()
    raise SystemExit(message)


def relaunch() -> None:
    cmd = (
        [sys.executable] if getattr(sys, "frozen", False)
        else [sys.executable, "-m", "actp.app"]
    )
    subprocess.Popen(cmd)


# ---- MT5 helpers used by the settings window ------------------------------

def detect_symbols() -> list[str]:
    """XAUUSD symbols the broker offers (empty if MT5 is not reachable)."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return []
    if not mt5.initialize():
        return []
    found = [s for s in (mt5.symbols_get("*XAU*") or ())
             if "XAUUSD" in s.name.upper()]
    # Best guess first: fully tradable, then in Market Watch, then the
    # shortest name (XAUUSDc before XAUUSD247c), then alphabetical.
    found.sort(key=lambda s: (s.trade_mode != 4, not s.visible,
                              len(s.name), s.name))
    return [s.name for s in found]


def check_against_broker(symbol: str, lot: Decimal) -> str | None:
    """A problem description, or None if the broker accepts this setup."""
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        return f"The MetaTrader5 package could not be loaded: {exc}"
    if not mt5.initialize():
        return "MT5 is not reachable. Open MT5, log in, and try again."
    account = mt5.account_info()
    if (
        account is not None
        and account.margin_mode != mt5.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING
    ):
        return "This is not a HEDGING account. ACTP needs a hedging account."
    mt5.symbol_select(symbol, True)
    info = mt5.symbol_info(symbol)
    if info is None:
        return f"Your broker has no symbol called {symbol}."
    if info.digits < 2:
        return f"{symbol} has {info.digits} price decimals; ACTP needs 2 or more."
    vmin = Decimal(str(info.volume_min))
    vstep = Decimal(str(info.volume_step))
    vmax = Decimal(str(info.volume_max))
    if not vmin <= lot <= vmax or (lot - vmin) % vstep != 0:
        return (
            f"Lot {lot} is not valid for {symbol} "
            f"(min {vmin}, step {vstep}, max {vmax})."
        )
    return None

def check_volumes(symbol: str, lots: list[Decimal]) -> str | None:
    """A problem description if the broker would refuse any of these lots."""
    if not lots:
        return None
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        return f"The MetaTrader5 package could not be loaded: {exc}"
    info = mt5.symbol_info(symbol)
    if info is None:
        return f"Cannot read {symbol} from MT5."
    vmin = Decimal(str(info.volume_min))
    vstep = Decimal(str(info.volume_step))
    vmax = Decimal(str(info.volume_max))
    for lot in sorted(set(lots)):
        if not vmin <= lot <= vmax or (lot - vmin) % vstep != 0:
            return (
                f"Lot {lot} is not valid for {symbol} "
                f"(min {vmin}, step {vstep}, max {vmax})."
            )
    return None

def check_order_limit(new_orders: int) -> str | None:
    """A problem description if the plan alone is bigger than the broker's
    pending-order limit (0 = no limit)."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return None
    account = mt5.account_info()
    limit = int(getattr(account, "limit_orders", 0) or 0)
    if limit and new_orders > limit:
        return (
            f"This plan has {new_orders} orders but your broker allows at most "
            f"{limit} pending orders on the account."
        )
    return None


def dark_title_bar(win: tk.Misc) -> None:
    """Windows 10/11: black title bar (the part with 'ACTP - v...')."""
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        on = ctypes.c_int(1)
        for attribute in (20, 19):  # 20 = Windows 11 / newer 10, 19 = older
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(on), ctypes.sizeof(on))
    except Exception:  # not Windows, or too old: the title bar stays light
        pass


def apply_theme(root: tk.Tk, name: str) -> None:
    """Dark colours for the plain Tk widgets (set before any window is
    built) and for the few ttk ones. Light = the normal system look."""
    if name != "dark":
        return
    p = theme.palette("dark")
    root.configure(bg=p["bg"])
    for pattern, value in {
        "*Background": p["bg"], "*Foreground": p["fg"],
        "*Entry.Background": p["entry"], "*Text.Background": p["entry"],
        "*Entry.insertBackground": p["fg"], "*Text.insertBackground": p["fg"],
        "*Button.Background": p["button"],
        "*Button.activeBackground": p["button_hover"],
        "*Button.activeForeground": p["fg"],
        "*Checkbutton.selectColor": p["entry"],
        "*Checkbutton.activeBackground": p["bg"],
        "*Checkbutton.activeForeground": p["fg"],
        "*disabledForeground": "#7b7f85",
        "*highlightBackground": p["bg"],
        "*TCombobox*Listbox.background": p["entry"],
        "*TCombobox*Listbox.foreground": p["fg"],
        "*TCombobox*Listbox.selectBackground": p["tab_accent"],
    }.items():
        root.option_add(pattern, value)
    style = ttk.Style(root)
    style.theme_use("clam")
    for name_ in ("TCombobox", "TSpinbox"):
        style.configure(name_, fieldbackground=p["entry"], foreground=p["fg"],
                        background=p["button"], arrowcolor=p["fg"],
                        bordercolor=p["border"], lightcolor=p["entry"],
                        darkcolor=p["entry"], insertcolor=p["fg"])
        style.map(name_, fieldbackground=[("readonly", p["entry"]),
                                          ("disabled", p["bg"])],
                  foreground=[("readonly", p["fg"]),
                              ("disabled", "#7b7f85")],
                  selectbackground=[("readonly", p["entry"])],
                  selectforeground=[("readonly", p["fg"])])
    style.configure("Vertical.TScrollbar", background=p["button"],
                    troughcolor=p["bg"], arrowcolor=p["fg"],
                    bordercolor=p["bg"], lightcolor=p["button"],
                    darkcolor=p["button"])
    dark_title_bar(root)

    original_init = tk.Toplevel.__init__

    def toplevel_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.after(20, lambda: dark_title_bar(self))

    tk.Toplevel.__init__ = toplevel_init


def install_language(code: str) -> None:
    """Show the window in Bahasa Indonesia: every text the windows and popups
    are given passes through i18n.tr(). English changes nothing at all."""
    i18n.set_language(code)
    if code != "id":
        return
    from tkinter import messagebox as mb

    original_options = tk.Misc._options

    def options(self, cnf, kw=None):
        try:
            if isinstance(cnf, dict) and "text" in cnf:
                cnf = dict(cnf, text=i18n.tr(cnf["text"]))
            if isinstance(kw, dict) and "text" in kw:
                kw = dict(kw, text=i18n.tr(kw["text"]))
        except Exception:  # a translation problem must never break the window
            pass
        return original_options(self, cnf, kw)

    tk.Misc._options = options

    original_title = tk.Wm.wm_title

    def wm_title(self, string=None):
        return original_title(
            self, i18n.tr(string) if isinstance(string, str) else string)

    tk.Wm.wm_title = wm_title
    tk.Wm.title = wm_title

    original_show = mb._show

    def show(title=None, message=None, *args, **kwargs):
        return original_show(i18n.tr(title), i18n.tr(message), *args, **kwargs)

    mb._show = show


class Tooltip:
    """A small text bubble shown after the mouse rests on a widget."""

    def __init__(self, widget: tk.Misc, text: str) -> None:
        self.widget = widget
        self.text = text
        self._job: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _enter(self, _event=None) -> None:
        self._hide()
        self._job = self.widget.after(350, self._show)

    def _show(self) -> None:
        self._job = None
        try:
            x = self.widget.winfo_rootx() + 8
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
            tip = tk.Toplevel(self.widget)
            tip.wm_overrideredirect(True)
            tip.wm_geometry(f"+{x}+{y}")
            tk.Label(tip, text=self.text, bg="#ffffe0", fg="black",
                     relief="solid", borderwidth=1, padx=4, pady=2).pack()
            self._tip = tip
        except tk.TclError:
            self._tip = None

    def _hide(self, _event=None) -> None:
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except (tk.TclError, ValueError):
                pass
            self._job = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


class CodeDialog:
    """Shows 5 random digits that must be typed to go on. The digits are
    drawn on a canvas (they cannot be selected or copied) and pasting into
    the box is blocked."""

    def __init__(self, parent: tk.Misc, title: str, message: str) -> None:
        self.ok = False
        self.code = confirm_logic.new_code()
        win = self.win = tk.Toplevel(parent)
        win.title(title)
        win.resizable(False, False)
        win.transient(parent)
        tk.Label(win, text=message, justify="left", wraplength=420
                 ).pack(padx=14, pady=(12, 6), anchor="w")
        tk.Label(win, text="Type the 5 digits below to confirm:"
                 ).pack(padx=14, anchor="w")
        canvas = tk.Canvas(win, width=190, height=56, bg="#f1f3f4",
                           highlightthickness=1,
                           highlightbackground=P["border"])
        canvas.pack(pady=6)
        rng = random.Random()
        for _ in range(6):  # a few lines across the digits
            canvas.create_line(
                rng.randrange(190), rng.randrange(56),
                rng.randrange(190), rng.randrange(56), fill="#c4c7c5")
        for i, digit in enumerate(self.code):
            canvas.create_text(
                22 + i * 36 + rng.randrange(-3, 4), 28 + rng.randrange(-5, 6),
                text=digit, fill="#202124",
                font=("Courier", rng.choice((22, 24, 26)), "bold"))
        self.typed = tk.StringVar()
        self.typed.trace_add("write", lambda *_: self.check())
        self.entry = tk.Entry(win, textvariable=self.typed, width=10,
                              justify="center", font=("TkDefaultFont", 14))
        self.entry.pack(pady=(0, 6))
        for seq in ("<<Paste>>", "<Control-v>", "<Control-V>",
                    "<Shift-Insert>", "<Button-2>"):
            self.entry.bind(seq, lambda _e: "break")
        row = tk.Frame(win)
        row.pack(pady=(0, 12))
        self.go = tk.Button(row, text="Confirm", width=10, state="disabled",
                            command=self.accept)
        self.go.pack(side="left", padx=4)
        tk.Button(row, text="Cancel", width=10, command=win.destroy).pack(
            side="left", padx=4)
        win.bind("<Return>", lambda _e: self.accept())
        win.bind("<Escape>", lambda _e: win.destroy())
        win.lift()
        win.wait_visibility()
        win.grab_set()
        self.entry.focus_set()
        parent.wait_window(win)

    def check(self) -> None:
        text = self.typed.get()
        if len(text) > confirm_logic.CODE_LENGTH:
            self.typed.set(text[:confirm_logic.CODE_LENGTH])
            return
        self.go.configure(
            state="normal" if confirm_logic.code_matches(text, self.code)
            else "disabled")

    def accept(self) -> None:
        if confirm_logic.code_matches(self.typed.get(), self.code):
            self.ok = True
            self.win.destroy()


def confirm_action(parent: tk.Misc, key: str, title: str, message: str) -> bool:
    """True when the person agreed. Confirmation ON for this action = the
    normal OK / Cancel popup; OFF (it was switched off in Settings, which
    needed the 5-digit code) = no popup, the action just runs."""
    if config.CONFIRM.get(key, True):
        return messagebox.askokcancel(title, message)
    return True


# Size of the big candle countdown in the box next to the Status lines
# (the number is the font size in points; smaller = a shorter box).
TIMER_FONT_SIZE = 16

TAB_ACCENT = P["tab_accent"]
TAB_IDLE_BG = P["tab_idle_bg"]
TAB_IDLE_FG = P["tab_idle_fg"]
TAB_HOVER_BG = P["tab_hover_bg"]


class SavedMultiLayerDialog:
    """Edit the saved MultiLayer list. Changes are kept in `presets` (a dict
    owned by the settings window) and written when Settings is saved."""

    def __init__(self, parent: tk.Misc, presets: dict[str, str]):
        self.presets = presets
        win = self.win = tk.Toplevel(parent)
        win.title("Saved MultiLayer")
        win.transient(parent)
        win.resizable(False, False)
        tk.Label(
            win, justify="left", fg=GREY, wraplength=420,
            text="Used when the MultiLayer box on the main window is empty "
                 "and the Layer amount is that number. A layer amount with "
                 "no entry here (or an empty one) uses 1x for every layer.",
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=8, pady=6)
        tk.Label(win, text="Layer", font=("TkDefaultFont", 9, "bold")).grid(
            row=1, column=0, padx=8, sticky="w")
        tk.Label(win, text="MultiLayer", font=("TkDefaultFont", 9, "bold")
                 ).grid(row=1, column=1, padx=8, sticky="w")
        self.body = tk.Frame(win)
        self.body.grid(row=2, column=0, columnspan=4, sticky="ew")
        self.rows: list[tuple[tk.StringVar, tk.StringVar, tk.Frame]] = []
        for key, value in presets.items():
            self.add_row(key, value)
        if not self.rows:
            self.add_row("", "")
        bar = tk.Frame(win)
        bar.grid(row=3, column=0, columnspan=4, sticky="ew", padx=8, pady=8)
        tk.Button(bar, text="Add new layer", command=lambda: self.add_row("", "")
                  ).pack(side="left")
        tk.Button(bar, text="Cancel", width=8, command=win.destroy).pack(side="right")
        tk.Button(bar, text="OK", width=8, command=self.ok).pack(
            side="right", padx=6)
        win.lift()
        win.wait_visibility()
        win.grab_set()

    def add_row(self, key: str, value: str) -> None:
        layer, multi = tk.StringVar(value=key), tk.StringVar(value=value)
        frame = tk.Frame(self.body)
        frame.pack(fill="x", padx=8, pady=2)
        tk.Entry(frame, textvariable=layer, width=6).pack(side="left")
        tk.Entry(frame, textvariable=multi, width=34).pack(side="left", padx=8)
        row = (layer, multi, frame)
        tk.Button(frame, text="Delete this layer",
                  command=lambda: self.delete_row(row)).pack(side="left")
        self.rows.append(row)

    def delete_row(self, row) -> None:
        row[2].destroy()
        self.rows.remove(row)

    def ok(self) -> None:
        raw: dict[str, str] = {}
        for layer, multi, _frame in self.rows:
            key = layer.get().strip()
            if not key and not multi.get().strip():
                continue  # a blank row
            if key in raw:
                messagebox.showerror(
                    "ACTP", f"Layer {key} appears twice.", parent=self.win)
                return
            raw[key] = multi.get()
        try:
            clean = cfg._presets(raw)
        except ValueError as exc:
            messagebox.showerror("ACTP", str(exc), parent=self.win)
            return
        self.presets.clear()
        self.presets.update(clean)
        self.win.destroy()


class SettingsDialog:
    def __init__(self, parent: tk.Misc, on_saved, first_run: bool = False):
        self.on_saved = on_saved
        self.saved = False
        self.first_run = first_run
        win = self.win = tk.Toplevel(parent)
        win.title("ACTP settings")
        win.resizable(False, False)
        if not first_run:
            win.transient(parent)

        self.symbol = tk.StringVar(value="" if first_run else config.SYMBOL)
        self.cap = tk.StringVar(value=str(config.MAX_MARKET_LAYERS_PER_SIDE))
        self.auto = tk.BooleanVar(value=config.AUTO_CUT_LOSS)
        self.hold = tk.StringVar(value=str(config.CUT_LOSS_HOLD_SECONDS))
        self.scl = tk.BooleanVar(value=config.SCL_ENABLED)
        self.pct = tk.StringVar(value=str(int(config.SCL_FRACTION * 100)))
        self.legacy = tk.BooleanVar(value=config.CUT_LOSS_MODE == "legacy")
        self.slm = tk.StringVar(value=format(
            config.STOP_LOSS_MULTIPLE.normalize(), "f"))
        self.presets = dict(config.SAVED_MULTILAYERS)
        self.ask = {key: tk.BooleanVar(value=config.CONFIRM.get(key, True))
                    for key in confirm_logic.ACTIONS}
        self.lang = tk.StringVar(value=i18n.LANGUAGES[config.LANGUAGE])
        self.ask_all = tk.BooleanVar(
            value=confirm_logic.parent_on(
                {k: v.get() for k, v in self.ask.items()}))

        pad = {"padx": 8, "pady": 4}
        tk.Label(win, text="XAUUSD symbol on your broker").grid(
            row=0, column=0, sticky="w", **pad)
        self.combo = ttk.Combobox(win, textvariable=self.symbol, width=18)
        self.combo.grid(row=0, column=1, **pad)
        tk.Button(win, text="Detect from MT5", command=self.detect).grid(
            row=0, column=2, **pad)

        # The lot per order is set on the main window, next to Layer amount.
        tk.Button(win, text="Saved MultiLayer...", command=self.open_presets
                  ).grid(row=1, column=0, sticky="w", **pad)

        sl_box = tk.Frame(win)
        sl_box.grid(row=2, column=2, **pad)
        tk.Label(sl_box, text="Manual SL = ").pack(side="left")
        tk.Entry(sl_box, textvariable=self.slm, width=5).pack(side="left")
        tk.Label(sl_box, text=" x range").pack(side="left")

        tk.Label(win, text="Max layers bought/sold at market per side").grid(
            row=2, column=0, sticky="w", **pad)
        ttk.Spinbox(win, from_=0, to=200, textvariable=self.cap, width=6).grid(
            row=2, column=1, sticky="w", **pad)

        ttk.Separator(win).grid(row=3, column=0, columnspan=3,
                                sticky="ew", pady=6)
        tk.Checkbutton(
            win, text="Auto cut loss (close a plan when its Cut Loss breaks)",
            variable=self.auto, command=self.sync,
        ).grid(row=4, column=0, columnspan=3, sticky="w", **pad)

        # The candle timeframe is set on the main window, next to Lot.
        self.legacy_check = tk.Checkbutton(
            win, text="      Use the legacy timer instead (hold time + SCL, "
                      "no candles)",
            variable=self.legacy, command=self.sync,
        )
        self.legacy_check.grid(row=6, column=0, columnspan=3, sticky="w", **pad)

        tk.Label(win, text="            Hold time before closing (seconds)"
                 ).grid(row=7, column=0, sticky="w", **pad)
        self.hold_box = ttk.Spinbox(win, from_=10, to=3600,
                                    textvariable=self.hold, width=8)
        self.hold_box.grid(row=7, column=1, sticky="w", **pad)

        self.scl_check = tk.Checkbutton(
            win, text="            Stop Cut Loss (SCL): price must also reach it",
            variable=self.scl, command=self.sync,
        )
        self.scl_check.grid(row=8, column=0, columnspan=3, sticky="w", **pad)
        tk.Label(win, text="            SCL distance (% of the way, Cut Loss "
                 "to SL)").grid(row=9, column=0, sticky="w", **pad)
        self.pct_box = ttk.Spinbox(win, from_=5, to=95,
                                   textvariable=self.pct, width=8)
        self.pct_box.grid(row=9, column=1, sticky="w", **pad)

        tk.Label(win, text="Language").grid(
            row=11, column=0, sticky="w", **pad)
        ttk.Combobox(win, textvariable=self.lang, state="readonly", width=18,
                     values=list(i18n.LANGUAGES.values())
                     ).grid(row=11, column=1, sticky="w", **pad)

        danger = tk.Frame(win, bg=DANGER_BG, highlightthickness=2,
                          highlightbackground=SELL_RED,
                          highlightcolor=SELL_RED)
        danger.grid(row=10, column=0, columnspan=3, sticky="ew",
                    padx=8, pady=6)
        tk.Label(danger, text="Popup configurations (Dangerous settings)",
                 bg=DANGER_BG, fg=SELL_RED,
                 font=("TkDefaultFont", 10, "bold")
                 ).pack(anchor="w", padx=8, pady=(6, 2))
        tk.Checkbutton(
            danger, text="Popup confirmation (OK / Cancel before each action)",
            variable=self.ask_all, command=self.ask_all_changed,
            bg=DANGER_BG, activebackground=DANGER_BG,
        ).pack(anchor="w", padx=8)
        tk.Button(danger, text="Configure each popup...",
                  command=self.open_popups).pack(anchor="w", padx=30, pady=4)
        tk.Label(
            danger, justify="left", fg=GREY, bg=DANGER_BG, wraplength=400,
            text="Unticking a box asks you to type a random 5-digit code. "
                 "An action with its box unticked runs with no popup at all.",
        ).pack(anchor="w", padx=8, pady=(0, 6))

        tk.Label(
            win, justify="left", fg=GREY, wraplength=430,
            text="Changes apply after ACTP restarts, and only to plans sent "
                 "afterwards: each plan keeps the cut loss rule and timeframe "
                 "it was sent with. With auto cut loss OFF, nothing closes a "
                 "plan automatically: only the broker's SL/TP, KILL ALL, or "
                 "you.",
        ).grid(row=12, column=0, columnspan=3, sticky="w", **pad)

        self.make_shortcut = tk.BooleanVar(value=True)
        if first_run:
            tk.Checkbutton(
                win, text="Create a desktop shortcut",
                variable=self.make_shortcut,
            ).grid(row=13, column=0, columnspan=3, sticky="w", **pad)

        row = tk.Frame(win)
        row.grid(row=14, column=0, columnspan=3, sticky="e", **pad)
        tk.Button(row, text="Save", width=10, command=self.save).pack(
            side="left", padx=4)
        tk.Button(row, text="Cancel", width=10, command=win.destroy).pack(
            side="left")

        self.sync()
        win.lift()
        win.wait_visibility()
        win.grab_set()
        if first_run:
            self.detect(quiet=True)

    def open_presets(self) -> None:
        SavedMultiLayerDialog(self.win, self.presets)

    def open_popups(self) -> None:
        """Sub window with one box per action."""
        win = tk.Toplevel(self.win)
        win.title("Popup configurations")
        win.resizable(False, False)
        win.transient(self.win)
        box = tk.Frame(win, bg=DANGER_BG, highlightthickness=2,
                       highlightbackground=SELL_RED)
        box.pack(fill="both", expand=True, padx=8, pady=8)
        tk.Label(box, text="Popup configurations (Dangerous settings)",
                 bg=DANGER_BG, fg=SELL_RED,
                 font=("TkDefaultFont", 10, "bold")
                 ).pack(anchor="w", padx=8, pady=(6, 4))
        for key, label in confirm_logic.ACTIONS.items():
            tk.Checkbutton(
                box, text=label, variable=self.ask[key],
                bg=DANGER_BG, activebackground=DANGER_BG,
                command=lambda k=key: self.ask_one_changed(k),
            ).pack(anchor="w", padx=22)
        tk.Label(
            box, justify="left", fg=GREY, bg=DANGER_BG, wraplength=340,
            text="Unticking a box asks you to type a random 5-digit code. "
                 "An action with its box unticked runs with no popup at all.",
        ).pack(anchor="w", padx=8, pady=6)
        tk.Button(win, text="Close", width=10, command=win.destroy
                  ).pack(pady=(0, 8))
        win.lift()
        win.wait_visibility()
        win.grab_set()
        self.win.wait_window(win)

    def code_ok(self, what: str) -> bool:
        return CodeDialog(
            self.win, "Turn off confirmation",
            f"Are you sure you want to untick {what}?\n\nWithout it, "
            "the action runs with no popup at all.").ok

    def ask_all_changed(self) -> None:
        if not self.ask_all.get() and not self.code_ok(
                "the popup confirmation of ALL actions"):
            self.ask_all.set(True)  # not confirmed: nothing changes
            return
        for var in self.ask.values():
            var.set(self.ask_all.get())

    def ask_one_changed(self, key: str) -> None:
        if not self.ask[key].get() and not self.code_ok(
                f"the popup confirmation of '{confirm_logic.ACTIONS[key]}'"):
            self.ask[key].set(True)
            return
        self.ask_all.set(confirm_logic.parent_on(
            {k: v.get() for k, v in self.ask.items()}))

    def sync(self) -> None:
        on = self.auto.get()
        old = on and self.legacy.get()
        self.legacy_check.configure(state="normal" if on else "disabled")
        self.hold_box.configure(state="normal" if old else "disabled")
        self.scl_check.configure(state="normal" if old else "disabled")
        self.pct_box.configure(
            state="normal" if old and self.scl.get() else "disabled")

    def detect(self, quiet: bool = False) -> None:
        names = detect_symbols()
        self.combo["values"] = names
        if not names:
            if not quiet:
                messagebox.showinfo(
                    "ACTP",
                    "No XAUUSD symbol found. Open MT5, log in, and try again, "
                    "or type the symbol name yourself.",
                    parent=self.win,
                )
            return
        if not self.symbol.get().strip():
            self.symbol.set(names[0])

    @staticmethod
    def current_theme() -> str:
        """The sun / moon button saves the theme to the file directly."""
        try:
            return cfg.load(config.SETTINGS_PATH)["theme"]
        except ValueError:
            return config.THEME

    @staticmethod
    def current_layer_amount() -> int:
        """The main window saves the last used Layer amount to the file;
        keep that value instead of the one read at start-up."""
        try:
            return cfg.load(config.SETTINGS_PATH)["layer_amount"]
        except ValueError:
            return config.LAYER_AMOUNT

    @staticmethod
    def current_auto_tp() -> bool:
        """The Auto-TP tick box saves itself to the file; keep that value."""
        try:
            return cfg.load(config.SETTINGS_PATH)["auto_tp"]
        except ValueError:
            return config.AUTO_TP

    def save(self) -> None:
        try:
            raw = {
                "symbol": self.symbol.get(),
                "lot_per_order": format(config.LOT_PER_ORDER, "f"),
                "auto_cut_loss": self.auto.get(),
                "cut_loss_hold_seconds": int(self.hold.get()),
                "scl_enabled": self.scl.get(),
                "scl_fraction": str(Decimal(self.pct.get()) / 100),
                "max_market_layers": int(self.cap.get()),
                "cut_loss_mode": "legacy" if self.legacy.get() else "candle",
                "cut_loss_timeframe": config.CUT_LOSS_TIMEFRAME,
                "stop_loss_multiple": self.slm.get(),
                "layer_amount": self.current_layer_amount(),
                "saved_multilayers": self.presets,
                "confirm": {k: v.get() for k, v in self.ask.items()},
                "theme": self.current_theme(),
                "auto_tp": self.current_auto_tp(),
                "language": next(
                    (c for c, n in i18n.LANGUAGES.items()
                     if n == self.lang.get()), "en"),
            }
            values = cfg.validate(raw)
        except (ValueError, InvalidOperation) as exc:
            messagebox.showerror("ACTP", str(exc), parent=self.win)
            return
        problem = check_against_broker(
            values["symbol"], Decimal(values["lot_per_order"])
        )
        if problem:
            messagebox.showerror("ACTP", problem, parent=self.win)
            return
        try:
            cfg.save(config.SETTINGS_PATH, values)
        except OSError as exc:
            messagebox.showerror("ACTP", f"Cannot save: {exc}", parent=self.win)
            return
        self.saved = True
        if self.first_run and self.make_shortcut.get():
            problem = shortcut.create_desktop_shortcut()
            if problem:
                messagebox.showwarning(
                    "ACTP", f"The desktop shortcut was not created:\n{problem}",
                    parent=self.win)
        self.win.destroy()
        self.on_saved()


def make_manager() -> tuple[ExecutionManager, Any, str, bool]:
    """Connect to the running MT5 terminal. Returns (manager, broker,
    banner, is_real_account)."""
    import MetaTrader5 as mt5  # Windows only, so imported here
    from actp.mt5_broker import Mt5Broker

    broker = Mt5Broker()
    broker.connect()
    account = mt5.account_info()
    if (
        account is not None
        and account.margin_mode != mt5.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING
    ):
        broker.shutdown()
        raise AccountNotSupported(
            "This account is not a HEDGING account.\n\n"
            "ACTP places several orders per side. On a netting account "
            "they merge into one position, which breaks Cut Loss and "
            "the close buttons.\n\n"
            "Open (or ask your broker for) a hedging account, then start "
            "ACTP again."
        )
    is_real = account is None or account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO
    if account is None:
        banner = "LIVE - No server found"
    elif is_real:
        banner = f"LIVE - SERVER - {account.server}"
    else:
        banner = f"DEMO ACCOUNT - {account.server}"
    return (
        ExecutionManager(
            broker, JsonPlanStore(STATE_PATH), check_stale=True,
            # With auto cut loss off nothing uses candles, so don't read them.
            cut_loss_mode=(
                config.CUT_LOSS_MODE if config.AUTO_CUT_LOSS else "legacy"),
            cut_loss_timeframe=config.CUT_LOSS_TIMEFRAME,
        ),
        broker,
        banner,
        is_real,
    )


class App:
    def __init__(
        self,
        root: tk.Tk,
        manager: ExecutionManager,
        broker: Any,
        banner: str,
        is_real: bool,
    ) -> None:
        self.file_log = logging.getLogger("actp")
        root.report_callback_exception = self.on_tk_error
        self.root = root
        self.manager = manager
        self.broker = broker
        self.dispatcher = Dispatcher(manager)
        self.auto_tp = tk.BooleanVar(value=config.AUTO_TP)
        self.auto_tp.trace_add("write", self.save_auto_tp)
        self._tp_last: dict[str, str] = {}
        self.monitor = CutLossMonitor(manager) if config.AUTO_CUT_LOSS else None
        self._last_ok: datetime | None = None
        self._chart = ChartLevelsWriter(config.CHART_LEVELS_PATH)
        self._chart_err_logged: str | None = None

        root.title(f"ACTP - v{VERSION}")
        root.geometry("720x900")
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        tk.Label(
            root, text=banner, fg="white", bg=DARK_BLUE if is_real else GREEN,
            font=("TkDefaultFont", 10, "bold"),
        ).pack(fill="x")
        tabs_area = tk.Frame(root)
        tabs_area.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        paste_tab, manual_tab = self.build_tabs(
            tabs_area, ("Paste message", "Manual Input"))
        self.box = scrolledtext.ScrolledText(paste_tab, height=10)
        self.box.pack(fill="both", expand=True)
        self.build_manual(manual_tab)

        multi_row = tk.Frame(root)
        multi_row.pack(fill="x", padx=8, pady=(6, 0))
        tk.Label(multi_row, text="MultiLayer:").pack(side="left")
        self.multi = tk.StringVar()
        # packed first so a narrow window never hides it
        theme_btn = tk.Button(
            multi_row, text="\u2600" if config.THEME == "dark" else "\u263e",
            font=("TkDefaultFont", 12), width=3, command=self.toggle_theme)
        theme_btn.pack(side="right")
        Tooltip(theme_btn, "Change theme")
        tk.Entry(multi_row, textvariable=self.multi, width=18).pack(
            side="left", padx=6)
        tk.Label(multi_row, text="Layer amount:").pack(side="left", padx=(10, 0))
        self.layers_var = tk.StringVar(value=str(config.LAYER_AMOUNT))
        self._last_amount = config.LAYER_AMOUNT
        self._persisted_amount = config.LAYER_AMOUNT
        amount = ttk.Spinbox(multi_row, from_=MIN_LAYERS, to=MAX_LAYERS,
                             textvariable=self.layers_var, width=4)
        amount.pack(side="left", padx=6)
        amount.bind("<FocusOut>", self.fix_layer_amount)
        tk.Label(multi_row, text="Lot:").pack(side="left", padx=(6, 0))
        self.lot_var = tk.StringVar(
            value=format(config.LOT_PER_ORDER, "f"))
        self._lot_text = self.lot_var.get()
        lot_box = tk.Entry(multi_row, textvariable=self.lot_var, width=4)
        lot_box.pack(side="left", padx=6)
        lot_box.bind("<FocusOut>", self.apply_lot)
        lot_box.bind("<Return>", self.apply_lot)
        tk.Label(multi_row, text="Candle:").pack(side="left", padx=(6, 0))
        self.tf_var = tk.StringVar(value=config.CUT_LOSS_TIMEFRAME)
        self.tf_main = ttk.Combobox(
            multi_row, textvariable=self.tf_var, width=4,
            values=TIMEFRAMES, state="readonly")
        self.tf_main.pack(side="left", padx=6)
        self.tf_main.bind("<<ComboboxSelected>>", self.apply_timeframe)
        Tooltip(self.tf_main, "Candle timeframe for NEW plans")
        if config.CUT_LOSS_MODE == "legacy" or not config.AUTO_CUT_LOSS:
            self.tf_main.configure(state="disabled")  # no candles in use
        tk.Label(multi_row, text="(MultiLayer wins if filled)",
                 fg=GREY).pack(side="left")
        self.multi_hint = tk.Label(root, anchor="w", justify="left",
                                   wraplength=640)
        self.multi_hint.pack(fill="x", padx=8)
        self.multi.trace_add("write", self.update_multi_hint)
        self.layers_var.trace_add("write", self.update_multi_hint)
        self.update_multi_hint()

        buttons = tk.Frame(root)
        buttons.pack(fill="x", padx=8, pady=6)
        self.send_btn = tk.Button(buttons, text="Send", width=12,
                                  command=self.send)
        self.send_btn.pack(side="left")
        self.clear_btn = tk.Button(buttons, text="Clear", width=8,
                                   command=self.clear)
        self.clear_btn.pack(side="left", padx=6)
        self.select_tab(0)
        tk.Button(buttons, text="Settings...", command=self.open_settings
                  ).pack(side="left")
        tk.Button(buttons, text="Reconcile (clear halt)",
                  command=self.reconcile).pack(side="right")
        tk.Button(buttons, text="KILL ALL", fg="white", bg=RED, padx=8,
                  command=self.kill).pack(side="right", padx=6)

        row2 = tk.Frame(root)
        row2.pack(fill="x", padx=8)
        tk.Button(row2, text="Close all positions",
                  command=lambda: self.close_positions_ask(False)
                  ).pack(side="left")
        tk.Button(row2, text="Close profitable only",
                  command=lambda: self.close_positions_ask(True)
                  ).pack(side="left", padx=6)
        tk.Button(row2, text="Cancel sell orders", fg="white",
                  bg=SELL_RED, command=lambda: self.cancel_side_ask("SELL")
                  ).pack(side="right")
        tk.Button(row2, text="Cancel buy orders", fg="white",
                  bg=BUY_GREEN, command=lambda: self.cancel_side_ask("BUY")
                  ).pack(side="right", padx=6)

        tp_row = tk.Frame(root)
        tp_row.pack(fill="x", padx=8)
        tk.Checkbutton(
            tp_row,
            text="Auto-TP: when all layers of a side fill, move TP to the "
                 "first entry",
            variable=self.auto_tp,
        ).pack(side="left")

        info = tk.Frame(root)
        info.pack(fill="x", padx=8, pady=2)
        left_col = tk.Frame(info)
        left_col.pack(side="left", fill="both", expand=True)
        status_rows = tk.Frame(left_col)
        status_rows.pack(fill="x", expand=True)  # centred beside the box
        self.status = tk.Label(status_rows, text="Status: running", anchor="w")
        self.status.pack(fill="x")
        self.monitor_label = tk.Label(status_rows, text="", anchor="w")
        self.monitor_label.pack(fill="x")
        timer_box = tk.Frame(info, highlightthickness=2,
                             highlightbackground=P["border"],
                             highlightcolor=P["border"])
        timer_box.pack(side="right", padx=(8, 0), ipadx=10)
        self.tf_title = tk.Label(timer_box, text="", fg=P["muted"])
        self.tf_title.pack()
        self.tf_big = tk.Label(timer_box, text="",
                               font=("TkDefaultFont", TIMER_FONT_SIZE, "bold"))
        self.tf_big.pack()
        self.tf_name = tk.Label(timer_box, text="", fg=P["muted"])
        self.tf_name.pack()

        tk.Label(root, text="Live plans (each plan has its own Cut Loss):"
                 ).pack(anchor="w", padx=8)
        self.build_plan_table(root)
        self._plans_read_at = 0.0
        self._plan_rows: list[dict[str, Any]] = []
        self._plan_widgets: dict[str, dict[str, Any]] = {}
        # timeframe -> (seconds left when read, time.monotonic() of the read)
        self._timer_base: dict[str, tuple[int | None, float]] = {}

        tk.Label(root, text="Log:").pack(anchor="w", padx=8)
        self.log_box = scrolledtext.ScrolledText(
            root, height=10, state="disabled")
        self.log_box.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.startup_check()
        self.log(
            f"STARTED: {banner}. Symbol {config.SYMBOL}, "
            f"lot {config.LOT_PER_ORDER}, auto cut loss "
            + ((f"ON (legacy timer {config.CUT_LOSS_HOLD_SECONDS}s, SCL "
                f"{'ON' if config.SCL_ENABLED else 'OFF'})"
                if config.CUT_LOSS_MODE == "legacy" else
                f"ON (candle close, {config.CUT_LOSS_TIMEFRAME})")
               if config.AUTO_CUT_LOSS else "OFF")
        )
        if self.monitor is None:
            self.log("WARN: auto cut loss is OFF. Positions are protected "
                     "only by their broker SL/TP.")
        root.after(POLL_MS, self.poll)
        root.after(1000, self.tick_timers)
        self.refresh_status()

    # ---- helpers ---------------------------------------------------------
    def log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{stamp}] {i18n.tr(text)}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        self.file_log.info(text.replace("\n", " | "))

    def on_tk_error(self, exc, val, tb) -> None:
        self.file_log.error("UNHANDLED ERROR", exc_info=(exc, val, tb))
        traceback.print_exception(exc, val, tb)

    def alarm(self) -> None:
        try:
            import winsound
            winsound.Beep(1200, 400)
        except Exception:
            self.root.bell()

    def refresh_status(self) -> None:
        if self.manager.halted:
            self.status.configure(
                text=f"Status: HALTED - {self.manager.halt_reason}", fg=RED_TEXT)
        else:
            self.status.configure(text="Status: running", fg=P["fg"])
        try:
            self.refresh_plans()
        except Exception as exc:  # the list must never break the window
            self.file_log.error("plan list failed: %r", exc)
        if self.monitor is None:
            self.monitor_label.configure(
                text="Auto cut loss: OFF (only broker SL/TP protect positions)",
                fg=RED_TEXT)
        elif self.monitor.error:
            self.monitor_label.configure(
                text=f"Cut-loss monitor: PROBLEM - {self.monitor.error}",
                fg=RED_TEXT)
        elif self._last_ok is None:
            self.monitor_label.configure(
                text="Cut-loss monitor: starting...", fg=P["fg"])
        else:
            self.monitor_label.configure(
                text="Cut-loss monitor: OK, last check "
                + self._last_ok.strftime("%H:%M:%S"), fg=GREEN_TEXT)

    def refresh_plans(self) -> None:
        """Fill the live-plans list. At most once per second."""
        now = time.monotonic()
        if now - self._plans_read_at < 1.0:
            return
        self._plans_read_at = now
        rows = self.manager.plans_overview()
        if rows is None:
            return  # keep what is shown; the monitor reports broker errors
        self._plan_rows = [
            {"iid": r["message_id"], "name": r["name"],
             "timeframe": r["timeframe"]} for r in rows
        ]
        frames = {r["timeframe"] for r in rows if r["timeframe"]}
        if self.monitor is not None and config.CUT_LOSS_MODE == "candle":
            frames.add(config.CUT_LOSS_TIMEFRAME)  # the countdown also runs
            #                                        while no plan is live
        if frames:
            stamp_now = time.monotonic()
            self._timer_base = {
                tf: (left, stamp_now)
                for tf, left in self.manager.candle_timers(frames).items()
            }
        self.fill_plan_table(rows)
        self.render_timers()

    def timer_left(self, timeframe: str | None) -> float | None:
        """Seconds until the candle closes, counted down locally between two
        reads of MT5 (None when unknown)."""
        if timeframe is None:
            return None
        base = self._timer_base.get(timeframe)
        if base is None or base[0] is None:
            return None
        left = base[0] - (time.monotonic() - base[1])
        if left <= 0:  # the candle closed: assume the next one is running
            left %= TIMEFRAME_SECONDS[timeframe]
        return left

    def timer_text(self, timeframe: str | None) -> str:
        """'M1 00:34'."""
        if timeframe is None:
            return "legacy timer"
        left = self.timer_left(timeframe)
        if left is None:
            return timeframe
        return f"{timeframe} {clock_text(int(left))}"

    def render_timers(self) -> None:
        for row in self._plan_rows:
            widgets = self._plan_widgets.get(row["iid"])
            if widgets is not None:
                widgets["cut"].configure(
                    text=self.timer_text(row["timeframe"]))
        colour = None  # None = the normal text colour
        name_text = ""
        if self.monitor is None:
            title, big = "Cut loss:", "OFF"
        elif self._plan_rows:
            newest = self._plan_rows[-1]
            name_text = f"({newest['name']})"
            if newest["timeframe"] is None:
                title = "Current cut loss:"
                big = f"{config.CUT_LOSS_HOLD_SECONDS}s"
            else:
                title = "Current timeframe used:"
                big = self.timer_text(newest["timeframe"])
                left = self.timer_left(newest["timeframe"])
                if left is not None:
                    colour = theme.timer_colour(
                        left, TIMEFRAME_SECONDS[newest["timeframe"]],
                        config.THEME)
        elif config.CUT_LOSS_MODE == "legacy":
            title, big = "Next plan uses:", "legacy timer"
        else:  # no plan yet: the countdown of the timeframe it will use
            frame = config.CUT_LOSS_TIMEFRAME
            title, big = "Current timeframe used:", self.timer_text(frame)
            name_text = "(next plan)"
            left = self.timer_left(frame)
            if left is not None:
                colour = theme.timer_colour(
                    left, TIMEFRAME_SECONDS[frame], config.THEME)
        self.tf_title.configure(text=title)
        self.tf_big.configure(text=big, fg=colour or P["fg"])
        self.tf_name.configure(text=name_text)

    def tick_timers(self) -> None:
        try:
            self.render_timers()
        except Exception as exc:  # the clock must never break the window
            self.file_log.error("timer failed: %r", exc)
        finally:
            self.root.after(1000, self.tick_timers)

    def clear(self) -> None:
        self.box.delete("1.0", "end")

    # ---- startup and polling ---------------------------------------------
    def startup_check(self) -> None:
        try:
            result = self.manager.reconcile()
        except ExecutionHalted as exc:
            self.log(f"STARTUP: {exc}")
            return
        self.log(
            f"Startup: {len(result.active_orders)} active ACTP orders."
        )

    def fix_layer_amount(self, *_args) -> None:
        """The Layer amount box never stays empty or invalid."""
        try:
            self._last_amount = parse_layer_amount(self.layers_var.get())
        except ValueError:
            pass
        self.layers_var.set(str(self._last_amount))

    def apply_timeframe(self, *_args) -> None:
        """Candle timeframe for plans sent from now on; saved for next start."""
        timeframe = self.tf_var.get()
        if timeframe == config.CUT_LOSS_TIMEFRAME:
            return
        try:
            self.manager.set_cut_loss_timeframe(timeframe)
            cfg.update(config.SETTINGS_PATH, cut_loss_timeframe=timeframe)
        except (ValueError, OSError) as exc:
            self.log(f"Candle timeframe not changed: {exc}")
            self.tf_var.set(config.CUT_LOSS_TIMEFRAME)
            return
        config.CUT_LOSS_TIMEFRAME = timeframe
        self.log(f"Candle timeframe for NEW plans is now {timeframe}. "
                 "Plans already sent keep theirs.")
        self.root.focus_set()
        self._plans_read_at = 0.0  # read the new timeframe's countdown now
        self.refresh_plans()
        self.render_timers()

    def apply_lot(self, *_args) -> bool:
        """Use the Lot box from now on (and keep it for the next start-up).
        A value the broker would refuse goes back to the last good one."""
        text = self.lot_var.get().strip()
        if text == self._lot_text:
            return True
        try:
            clean = cfg.validate({"lot_per_order": text})["lot_per_order"]
            problem = check_volumes(config.SYMBOL, [Decimal(clean)])
            if problem:
                raise ValueError(problem)
            cfg.update(config.SETTINGS_PATH, lot_per_order=clean)
        except (ValueError, InvalidOperation, OSError) as exc:
            self.log(f"Lot not changed: {exc}")
            self.lot_var.set(self._lot_text)
            return False
        config.LOT_PER_ORDER = Decimal(clean)
        self._lot_text = clean
        self.lot_var.set(clean)
        self.log(f"Lot per order is now {clean}.")
        self.update_multi_hint()
        return True

    def update_multi_hint(self, *_args) -> None:
        try:
            weights = resolve_weights(
                self.multi.get(), self.layers_var.get(), config.SAVED_MULTILAYERS)
            lots = lots_for_weights(weights, config.LOT_PER_ORDER)
        except ValueError as exc:
            self.multi_hint.configure(text=str(exc), fg=RED_TEXT)
            return
        typed = bool(self.multi.get().strip())
        saved = str(len(weights)) in config.SAVED_MULTILAYERS
        source = ("typed MultiLayer" if typed
                  else "saved MultiLayer" if saved else "default 1x")
        self.multi_hint.configure(
            text=f"{len(lots)} layers per side ({source}); layer 1 first: "
                 + summarize_lots(lots)
                 + f"  (total {sum(lots)} per side)",
            fg=GREY if source == "default 1x" else P["orange"],
        )

    def sync_chart(self) -> None:
        """Keep the Cut Loss / SCL lines on the MT5 chart current."""
        try:
            levels = self.manager.chart_levels() if config.AUTO_CUT_LOSS else {}
            if levels is not None and not self._chart.sync(levels):
                if self._chart.error and self._chart.error != self._chart_err_logged:
                    self._chart_err_logged = self._chart.error
                    self.log(f"WARN: chart lines not written: {self._chart.error}")
        except Exception as exc:  # never let drawing break the monitor
            self.log(f"WARN: chart lines failed: {exc!r}")

    def poll(self) -> None:
        try:
            if self.monitor is not None:
                for event in self.monitor.poll():
                    self.log(f"{event.level.upper()}: {event.message}")
                    if event.level == "alert":
                        self.alarm()
                if self.monitor.error is None:
                    self._last_ok = datetime.now()
            self.sync_chart()
            if self.auto_tp.get():
                self.run_auto_tp()
            self.refresh_status()
        except Exception as exc:  # the loop must never die
            self.log(f"MONITOR ERROR: {exc!r}")
        finally:
            self.root.after(POLL_MS, self.poll)

    # ---- sending a pasted plan ---------------------------------------------
    def send(self) -> None:
        """Send the pasted message."""
        text = self.box.get("1.0", "end").strip()
        if not text:
            return
        self.submit(text, self.clear)

    def submit(self, text: str, on_success) -> None:
        """Preview, confirm and send one message (pasted or built from the
        Manual Input form). on_success() clears the form it came from."""
        if not self.apply_lot():
            self.log("NOT SENT: fix the Lot box first.")
            return
        weights = None
        try:
            is_plan = parse_message(text)["type"] == "PLAN"
        except ValueError as exc:
            self.log(f"NOT SENT: {exc}")
            return
        if is_plan:  # UPDATE / CLOSE ignore the layer boxes
            try:
                weights = resolve_weights(
                    self.multi.get(), self.layers_var.get(),
                    config.SAVED_MULTILAYERS)
            except ValueError as exc:
                self.log(f"NOT SENT: MultiLayer / Layer amount: {exc}")
                return

        try:
            preview = self.dispatcher.preview(text, weights)
            lots = self.dispatcher.lots(text, weights)
            problem = check_volumes(config.SYMBOL, lots) or check_order_limit(
                len(lots))
        except (ValueError, ExecutionHalted) as exc:
            self.log(f"NOT SENT: {exc}")
            self.refresh_status()
            return

        if problem:
            self.log(f"NOT SENT: {problem}")
            return

        self.file_log.info("PREVIEW: %s", preview.replace("\n", " | "))
        if not confirm_action(
            self.root, "send", "Confirm",
            f"{preview}\n\nSend this to your account?"
        ):
            self.log("Cancelled.")
            return
        self.log("Confirmed by user.")
        try:
            result = self.dispatcher.dispatch(text, weights)
        except ExecutionHalted as exc:
            self.log(
                f"HALTED: {exc}\nPress Reconcile, then Send the same message "
                "again to resume."
            )
            self.alarm()
        except ValueError as exc:
            self.log(f"NOT SENT: {exc}")
        except Exception as exc:
            self.log(f"ERROR (check the log and MT5 before retrying): {exc!r}")
            self.alarm()
        else:
            self.log(result.summary)
            on_success()
            if is_plan:
                self.remember_layer_amount()
        self.refresh_status()

    # ---- Manual Input tab ----------------------------------------------------
    def build_manual(self, tab: tk.Misc) -> None:
        self.mfields: dict[str, tk.StringVar] = {}
        self.mnotes: dict[str, tk.Label] = {}
        grey = {"fg": GREY, "anchor": "w", "justify": "left"}
        for block, side in enumerate(reversed(SIDES)):  # sell on top
            top = block * 4
            color = BUY_GREEN if side == "buy" else SELL_RED
            tk.Label(tab, text=f"{side} range:", fg=color,
                     font=("TkDefaultFont", 9, "bold")).grid(
                row=top, column=0, sticky="w", padx=6, pady=(6, 0))
            rng = tk.Frame(tab)
            rng.grid(row=top, column=1, sticky="w", pady=(6, 0))
            for key, tail in (("a", " - "), ("b", "")):
                var = self.mfields[f"{side}_{key}"] = tk.StringVar()
                tk.Entry(rng, textvariable=var, width=11).pack(side="left")
                if tail:
                    tk.Label(rng, text=tail).pack(side="left")
            for offset, (key, label) in enumerate(
                (("cl", "cut loss"), ("sl", "stop loss"), ("tp", "take profit")),
                start=1,
            ):
                tk.Label(tab, text=f"{side} {label}:").grid(
                    row=top + offset, column=0, sticky="w", padx=6)
                var = self.mfields[f"{side}_{key}"] = tk.StringVar()
                tk.Entry(tab, textvariable=var, width=11).grid(
                    row=top + offset, column=1, sticky="w")
                note = tk.Label(tab, text="", **grey)
                note.grid(row=top + offset, column=2, sticky="w", padx=8)
                self.mnotes[f"{side}_{key}"] = note
        buttons = tk.Frame(tab)
        buttons.grid(row=8, column=0, columnspan=3, sticky="w", padx=6, pady=8)
        tk.Button(buttons, text="add buy PO range", fg="white", bg=BUY_GREEN,
                  command=lambda: self.send_manual("buy")).pack(side="left")
        tk.Button(buttons, text="add sell PO range", fg="white", bg=SELL_RED,
                  command=lambda: self.send_manual("sell")).pack(
            side="left", padx=6)
        tk.Button(buttons, text="add both buy and sell PO range",
                  command=lambda: self.send_manual("both")).pack(side="left")
        tk.Button(buttons, text="Clear fields", command=self.clear_manual
                  ).pack(side="left", padx=6)
        tk.Label(
            tab, justify="left", fg=GREY, wraplength=640,
            text="Layers and lots come from the MultiLayer / Layer amount "
                 "boxes below. Leave a price empty to use the default shown "
                 "next to it. A side with an empty range is left out.",
        ).grid(row=9, column=0, columnspan=3, sticky="w", padx=6)
        for var in self.mfields.values():
            var.trace_add("write", self.update_manual_notes)
        self.update_manual_notes()

    def manual_values(self) -> dict[str, str]:
        return {key: var.get() for key, var in self.mfields.items()}

    def update_manual_notes(self, *_args) -> None:
        values = self.manual_values()
        for side in SIDES:
            notes = default_notes(side, values, config.STOP_LOSS_MULTIPLE)
            for key in ("cl", "sl", "tp"):
                self.mnotes[f"{side}_{key}"].configure(text=notes[key])

    def clear_manual(self) -> None:
        for var in self.mfields.values():
            var.set("")

    def build_tabs(self, parent: tk.Misc, titles: tuple[str, str]):
        """Two tabs that share the width 50/50; the open one looks joined to
        its page (page colour, bold text, coloured top strip), the other
        one is darker, like browser tabs."""
        self._tab_body_bg = self.root.cget("bg")
        bar = tk.Frame(parent)
        bar.pack(fill="x")
        body = tk.Frame(parent, highlightthickness=1,
                        highlightbackground=P["border"])
        body.pack(fill="both", expand=True)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        self._tab_cells: list[dict[str, Any]] = []
        pages = []
        for i, title in enumerate(titles):
            bar.grid_columnconfigure(i, weight=1, uniform="tabs")
            cell = tk.Frame(bar, cursor="hand2")
            cell.grid(row=0, column=i, sticky="ew")
            strip = tk.Frame(cell, height=3)
            strip.pack(fill="x")
            label = tk.Label(cell, text=title, pady=7,
                             font=("TkDefaultFont", 10, "bold"))
            label.pack(fill="x")
            for widget in (cell, strip, label):
                widget.bind("<Button-1>", lambda _e, n=i: self.select_tab(n))
                widget.bind("<Enter>", lambda _e, n=i: self._tab_hover(n, True))
                widget.bind("<Leave>", lambda _e, n=i: self._tab_hover(n, False))
            self._tab_cells.append(
                {"cell": cell, "strip": strip, "label": label})
            page = tk.Frame(body)
            page.grid(row=0, column=0, sticky="nsew")
            pages.append(page)
        self._tab_pages = pages
        self._tab_index = 0
        self.select_tab(0)
        return pages

    def _paint_tab(self, n: int, hover: bool = False) -> None:
        parts = self._tab_cells[n]
        if n == self._tab_index:
            bg, fg, strip = self._tab_body_bg, P["fg"], TAB_ACCENT
        else:
            bg = TAB_HOVER_BG if hover else TAB_IDLE_BG
            fg, strip = TAB_IDLE_FG, bg
        parts["cell"].configure(bg=bg)
        parts["label"].configure(bg=bg, fg=fg)
        parts["strip"].configure(bg=strip)

    def _tab_hover(self, n: int, inside: bool) -> None:
        self._paint_tab(n, hover=inside)

    def select_tab(self, n: int) -> None:
        """Show page n. Send / Clear belong to the Paste tab."""
        if not hasattr(self, "_tab_pages"):
            return
        self._tab_index = n
        self._tab_pages[n].tkraise()
        for i in range(len(self._tab_cells)):
            self._paint_tab(i)
        if not hasattr(self, "clear_btn"):
            return  # the window is still being built
        state = "disabled" if n == 1 else "normal"
        self.send_btn.configure(state=state)
        self.clear_btn.configure(state=state)

    # ---- live plans table ------------------------------------------------

    # column: (minsize, weight)
    PLAN_COLS = ((78, 0), (66, 0), (150, 1), (30, 0),
                 (150, 1), (30, 0), (96, 0), (30, 0))

    def build_plan_table(self, parent: tk.Misc) -> None:
        box = tk.Frame(parent, highlightthickness=1,
                       highlightbackground=P["border"])
        box.pack(fill="x", padx=8, pady=(0, 4))
        head = tk.Frame(box, bg=P["head_bg"])
        head.pack(fill="x")
        titles = ("Plan", "Sent", "BUY (filled / pending, CL)", "",
                  "SELL (filled / pending, CL)", "", "Cut loss candle", "")
        for c, (size, weight) in enumerate(self.PLAN_COLS):
            head.grid_columnconfigure(c, minsize=size, weight=weight)
            tk.Label(head, text=titles[c], bg=P["head_bg"], anchor="w",
                     font=("TkDefaultFont", 9, "bold")).grid(
                row=0, column=c, sticky="ew", padx=2)
        head.grid_columnconfigure(len(self.PLAN_COLS), minsize=17)
        area = tk.Frame(box)
        area.pack(fill="x")
        self._plan_canvas = tk.Canvas(area, height=104, highlightthickness=0)
        bar = ttk.Scrollbar(area, orient="vertical",
                            command=self._plan_canvas.yview)
        self._plan_canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self._plan_canvas.pack(side="left", fill="x", expand=True)
        self._plan_body = tk.Frame(self._plan_canvas)
        window = self._plan_canvas.create_window(
            (0, 0), window=self._plan_body, anchor="nw")
        for c, (size, weight) in enumerate(self.PLAN_COLS):
            self._plan_body.grid_columnconfigure(
                c, minsize=size, weight=weight)
        self._plan_canvas.bind(
            "<Configure>",
            lambda e: self._plan_canvas.itemconfigure(window, width=e.width))
        self._plan_body.bind(
            "<Configure>",
            lambda _e: self._plan_canvas.configure(
                scrollregion=self._plan_canvas.bbox("all")))
        self._plan_canvas.bind(
            "<MouseWheel>",
            lambda e: self._plan_canvas.yview_scroll(
                -1 if e.delta > 0 else 1, "units"))
        self._plan_empty = tk.Label(
            self._plan_body, text="No live plans.", fg=P["muted"], anchor="w")
        self._plan_empty.grid(row=0, column=0, columnspan=8, sticky="w",
                              padx=4, pady=4)

    def _cell_button(self, name_key: str, row: dict[str, Any],
                     colour: str, side: str | None, grid_row: int,
                     column: int) -> tk.Button:
        plan = row["name"]
        if side is None:
            tip = f"Cancel entire plan and close all positions ({plan})"
        else:
            word = side.lower()
            tip = (f"Cancel PO {word} and close {word} position ({plan})")
        button = tk.Button(
            self._plan_body, text="\u2715", width=2, bg=colour, fg="white",
            activebackground=colour, activeforeground="white",
            relief="flat", cursor="hand2",
            command=lambda: self.close_plan_ask(row["message_id"], plan, side))
        button.grid(row=grid_row, column=column, padx=2, pady=1)
        Tooltip(button, tip)
        return button

    def fill_plan_table(self, rows: list[dict[str, Any]]) -> None:
        """Update the rows in place; rows of finished plans disappear."""
        live = {r["message_id"] for r in rows}
        for mid in [m for m in self._plan_widgets if m not in live]:
            for widget in self._plan_widgets.pop(mid)["all"]:
                widget.destroy()
        if rows:
            self._plan_empty.grid_remove()
        else:
            self._plan_empty.grid()
        for index, row in enumerate(rows):
            mid = row["message_id"]
            grid_row = index + 1
            entry = self._plan_widgets.get(mid)
            if entry is None:
                body = self._plan_body
                entry = {
                    "name": tk.Label(body, anchor="w",
                                     font=("TkDefaultFont", 9, "bold")),
                    "sent": tk.Label(body, anchor="w"),
                    "BUY": tk.Label(body, anchor="w"),
                    "SELL": tk.Label(body, anchor="w"),
                    "cut": tk.Label(body, anchor="w"),
                }
                entry["btn_BUY"] = self._cell_button(
                    "btn_BUY", row, BUY_GREEN, "BUY", grid_row, 3)
                entry["btn_SELL"] = self._cell_button(
                    "btn_SELL", row, SELL_RED, "SELL", grid_row, 5)
                entry["btn_ALL"] = self._cell_button(
                    "btn_ALL", row, P["plan_btn"], None, grid_row, 7)
                entry["all"] = [w for k, w in entry.items()
                                if k != "all"]
                self._plan_widgets[mid] = entry
            for widget, column in ((entry["name"], 0), (entry["sent"], 1),
                                   (entry["BUY"], 2), (entry["SELL"], 4),
                                   (entry["cut"], 6)):
                widget.grid(row=grid_row, column=column, sticky="ew", padx=2)
            for key, column in (("btn_BUY", 3), ("btn_SELL", 5),
                                ("btn_ALL", 7)):
                entry[key].grid(row=grid_row, column=column, padx=2, pady=1)
            sent = row["sent_at"]
            entry["name"].configure(text=row["name"])
            entry["sent"].configure(
                text=datetime.fromtimestamp(sent).strftime("%H:%M:%S")
                if sent else "-")
            for side in ("BUY", "SELL"):
                part = row.get(side)
                entry[side].configure(
                    text="-" if part is None else
                    f"{part['filled']} / {part['pending']}"
                    + (f", CL {part['cut_loss']}" if part["cut_loss"] else ""))
                if part is None:
                    entry[f"btn_{side}"].grid_remove()
            entry["cut"].configure(text=self.timer_text(row["timeframe"]))

    def close_plan_ask(self, message_id: str, name: str,
                       side: str | None = None) -> None:
        """Cancel pending + close open positions of one plan (or one side)."""
        snap = self.manager.snapshot()
        if "error" in snap:
            self.log(f"MT5 read failed: {snap['error']}")
            return
        mine = [o for o in snap["active"]
                if plan_id(o) == message_id and (side is None or o.side == side)]
        pending = sum(1 for o in mine if o.status.value == "PENDING")
        opened = sum(1 for o in mine if o.status.value == "FILLED")
        what = f"{side} side of plan {name}" if side else f"entire plan {name}"
        if pending == 0 and opened == 0:
            self.log(f"Nothing to close in the {what}.")
            return
        if not confirm_action(
            self.root, "close_plan" if side is None else "close_plan_side",
            "Close plan" if side is None else f"Close {side}",
            f"Cancel {pending} pending order(s) and close {opened} open "
            f"position(s) of the {what}?\n\nOther plans are not touched.",
        ):
            return
        try:
            result = self.manager.close_plan(message_id, side)
        except ExecutionHalted as exc:
            self.log(f"CLOSE FAILED: {exc}\nCheck MT5 by hand NOW.")
            self.alarm()
        except Exception as exc:
            self.log(f"CLOSE ERROR: {exc!r}\nCheck MT5 by hand NOW.")
            self.alarm()
        else:
            self.log(f"Closed {what}: {result['cancelled']} cancelled, "
                     f"{result['closed']} closed.")
        finally:
            self.dispatcher.forget_pending()
            self.refresh_status()
            self._plans_read_at = 0.0
            self.refresh_plans()

    def send_manual(self, which: str) -> None:
        try:
            sides = collect(which, self.manual_values(), config.STOP_LOSS_MULTIPLE)
            layers = len(resolve_weights(
                self.multi.get(), self.layers_var.get(),
                config.SAVED_MULTILAYERS))
        except ValueError as exc:
            self.log(f"NOT SENT: {exc}")
            return
        self.submit(build_plan_text(sides, layers), self.clear_manual)

    def remember_layer_amount(self) -> None:
        """Keep the last Layer amount for the next start-up."""
        try:
            amount = parse_layer_amount(self.layers_var.get())
            if amount != self._persisted_amount:
                cfg.update(config.SETTINGS_PATH, layer_amount=amount)
                self._persisted_amount = amount
        except (ValueError, OSError) as exc:
            self.log(f"WARN: could not save the Layer amount: {exc}")

    def save_auto_tp(self, *_args) -> None:
        """Remember the Auto-TP tick box for the next start-up."""
        try:
            cfg.update(config.SETTINGS_PATH, auto_tp=bool(self.auto_tp.get()))
        except (ValueError, OSError, tk.TclError) as exc:
            self.log(f"WARN: could not save the Auto-TP choice: {exc}")

    # ---- auto TP ---------------------------------------------------------
    def run_auto_tp(self) -> None:
        try:
            results = self.manager.move_tp_when_full()
        except ExecutionHalted as exc:
            self.log(f"ALERT: Auto-TP halted the manager: {exc}")
            self.alarm()
            return
        for side, (state, target, count) in results.items():
            if self._tp_last.get(side) == state:
                continue  # report each state once
            if state == "MOVED":
                self.log(f"AUTO-TP: all {side} layers filled. TP moved to "
                         f"{target} on {count} positions.")
            elif state == "PRICE_PAST":
                self.log(f"AUTO-TP: all {side} layers filled, but price is "
                         f"already past {target}. TP not moved yet; retrying.")
            else:
                self.log(f"AUTO-TP: MT5 refused the TP change on {side} "
                         f"({count} moved). Retrying.")
        self._tp_last = {s: r[0] for s, r in results.items()}

    # ---- settings, reconcile, kill, close, quit -------------------------------
    def open_settings(self) -> None:
        SettingsDialog(self.root, self.on_settings_saved)

    def toggle_theme(self) -> None:
        new = "light" if config.THEME == "dark" else "dark"
        try:
            cfg.update(config.SETTINGS_PATH, theme=new)
        except (OSError, ValueError) as exc:
            self.log(f"Cannot save the theme: {exc}")
            return
        if messagebox.askyesno(
            "Restart",
            "Theme changed. It applies after a restart.\n\nRestart ACTP "
            "now? Cut Loss monitoring pauses for a few seconds; open orders "
            "keep their broker SL/TP.",
        ):
            self.restart()

    def on_settings_saved(self) -> None:
        if messagebox.askyesno(
            "Restart",
            "Settings saved. They apply after a restart.\n\nRestart ACTP now? "
            "Cut Loss monitoring pauses for a few seconds; open orders keep "
            "their broker SL/TP.",
        ):
            self.restart()

    def restart(self) -> None:
        self.log("RESTARTING to apply settings.")
        self.shutdown_broker()
        self.root.destroy()
        relaunch()

    def shutdown_broker(self) -> None:
        try:
            self.broker.shutdown()
        except Exception:
            pass

    def reconcile(self) -> None:
        try:
            self.broker.connect()  # re-attach if the terminal restarted
        except BrokerError as exc:
            self.log(f"Reconnect failed: {exc}")
        try:
            result = self.manager.reconcile()
        except ExecutionHalted as exc:
            self.log(str(exc))
        else:
            self.log(
                f"Reconciled. {len(result.active_orders)} active orders."
            )
        self.refresh_status()

    def kill(self) -> None:
        if not confirm_action(
            self.root, "kill", "KILL ALL",
            "Cancel every pending ACTP order and close every open ACTP "
            "position?\n\n(Orders not placed by ACTP are not touched.)",
        ):
            return
        try:
            result = self.manager.close_all()
        except ExecutionHalted as exc:
            self.log(f"KILL FAILED: {exc}\nCheck MT5 by hand NOW.")
            self.alarm()
        except Exception as exc:
            self.log(f"KILL ERROR: {exc!r}\nCheck MT5 by hand NOW.")
            self.alarm()
        else:
            self.log(f"KILL: {result['cancelled']} cancelled, "
                     f"{result['closed']} closed.")
        finally:
            # A half-applied plan must not be resumed by re-sending it.
            self.dispatcher.forget_pending()
            self.refresh_status()

    def cancel_side_ask(self, side: str) -> None:
        snap = self.manager.snapshot()
        if "error" in snap:
            self.log(f"MT5 read failed: {snap['error']}")
            return
        pending = sum(1 for o in snap["active"]
                      if o.side == side and o.status.value == "PENDING")
        opened = sum(1 for o in snap["active"]
                     if o.side == side and o.status.value == "FILLED")
        if pending == 0:
            self.log(f"No pending {side} orders to cancel.")
            return
        if not confirm_action(
            self.root, "cancel_orders", "Cancel orders",
            f"Cancel {pending} pending {side} order(s) (all plans)?\n\n"
            f"Open {side} positions ({opened}) are NOT touched.",
        ):
            return
        try:
            result = self.manager.cancel_pending_side(side)
        except ExecutionHalted as exc:
            self.log(f"CANCEL FAILED: {exc}\nCheck MT5 by hand.")
            self.alarm()
        except Exception as exc:
            self.log(f"CANCEL ERROR: {exc!r}\nCheck MT5 by hand.")
            self.alarm()
        else:
            self.log(
                f"Cancel {side}: {result['cancelled']} pending cancelled, "
                f"{result['left_open']} open positions left."
            )
        finally:
            # A half-applied plan must not be resumed by re-sending it.
            self.dispatcher.forget_pending()
            self.refresh_status()

    def count_positions(self, only_profitable: bool) -> tuple[int, str]:
        snap = self.manager.snapshot()
        if "error" in snap:
            return 0, f"MT5 read failed: {snap['error']}"
        opened = [o for o in snap["active"] if o.status.value == "FILLED"]
        if only_profitable:
            tick = snap["tick"]
            if tick is None:
                return 0, "No price from MT5; cannot tell which are in profit."
            opened = [o for o in opened if position_in_profit(o, tick)]
        return len(opened), ""

    def close_positions_ask(self, only_profitable: bool) -> None:
        count, problem = self.count_positions(only_profitable)
        if problem:
            self.log(problem)
            return
        what = "profitable " if only_profitable else ""
        if count == 0:
            self.log(f"No {what}open positions to close.")
            return
        if not confirm_action(
            self.root, "close_profitable" if only_profitable else "close_all",
            "Close positions",
            f"Close {count} {what}open ACTP position(s)?\n\n"
            "Pending orders are NOT touched.",
        ):
            return
        label = "profitable positions" if only_profitable else "all positions"
        try:
            result = self.manager.close_positions(only_profitable)
        except Exception as exc:
            self.log(f"CLOSE ERROR: {exc}\nCheck MT5 by hand NOW.")
            self.alarm()
        else:
            self.log(
                f"Close {label}: {result['closed']} closed, "
                f"{result['left_open']} left open, "
                f"{result['left_pending']} pending orders left in place."
            )
        self.refresh_status()

    def on_close(self) -> None:
        if not confirm_action(
            self.root, "quit", "Quit",
            "Closing this window stops Cut Loss and Auto-TP monitoring. "
            "Orders keep their own SL/TP at the broker.\n\nQuit anyway?",
        ):
            return
        self.log("STOPPED: window closed.")
        self.shutdown_broker()
        self.root.destroy()


def first_run() -> None:
    root = tk.Tk()
    root.withdraw()
    if not messagebox.askokcancel("ACTP - read first", RISK_TEXT):
        root.destroy()
        return
    dialog = SettingsDialog(root, on_saved=lambda: None, first_run=True)
    root.wait_window(dialog.win)
    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:
        pass
    root.destroy()
    if dialog.saved:
        relaunch()


def main() -> None:
    logo.set_taskbar_identity()
    install_language(config.LANGUAGE)
    try:
        setup_file_log()
    except OSError as exc:
        fatal(f"Cannot create the log file: {exc}")
    if config.SETTINGS_ERROR:
        fatal(
            f"Cannot read your settings file:\n{config.SETTINGS_ERROR}\n\n"
            "Fix or delete it, then start ACTP again."
        )
    if config.FIRST_RUN:
        first_run()
        return
    try:
        manager, broker, banner, is_real = make_manager()
    except AccountNotSupported as exc:
        fatal(str(exc))
    except (BrokerError, RuntimeError, ImportError) as exc:
        fatal(
            f"Cannot start: {exc}\n\nOpen MetaTrader 5, log in, turn on "
            "Algo Trading, then start ACTP again."
        )
    root = tk.Tk()
    apply_theme(root, config.THEME)
    logo_problem = logo.apply(root)
    app = App(root, manager, broker, banner, is_real)
    if logo_problem:
        app.log(f"WARN: {logo_problem}")
    root.mainloop()


if __name__ == "__main__":
    main()