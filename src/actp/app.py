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
from actp.dispatcher import Dispatcher
from actp.execution import ExecutionHalted, ExecutionManager, position_in_profit
from actp.monitor import CutLossMonitor
from actp.plan_store import JsonPlanStore

STATE_PATH = config.APP_DIR / "state" / "actp_state.json"
LOG_PATH = config.APP_DIR / "logs" / "actp.log"
# Must stay well below MAX_POLL_GAP_SECONDS (15) in config.py.
POLL_MS = 3000
RED = "#b00020"
GREEN = "#1b5e20"
GREY = "#37474f"

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
    found = mt5.symbols_get("*XAU*") or ()
    return sorted(s.name for s in found if "XAUUSD" in s.name.upper())


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


class SettingsDialog:
    def __init__(self, parent: tk.Misc, on_saved, first_run: bool = False):
        self.on_saved = on_saved
        self.saved = False
        win = self.win = tk.Toplevel(parent)
        win.title("ACTP settings")
        win.resizable(False, False)
        if not first_run:
            win.transient(parent)

        self.symbol = tk.StringVar(value="" if first_run else config.SYMBOL)
        self.lot = tk.StringVar(value=str(config.LOT_PER_ORDER))
        self.cap = tk.StringVar(value=str(config.MAX_MARKET_LAYERS_PER_SIDE))
        self.auto = tk.BooleanVar(value=config.AUTO_CUT_LOSS)
        self.hold = tk.StringVar(value=str(config.CUT_LOSS_HOLD_SECONDS))
        self.scl = tk.BooleanVar(value=config.SCL_ENABLED)
        self.pct = tk.StringVar(value=str(int(config.SCL_FRACTION * 100)))

        pad = {"padx": 8, "pady": 4}
        tk.Label(win, text="XAUUSD symbol on your broker").grid(
            row=0, column=0, sticky="w", **pad)
        self.combo = ttk.Combobox(win, textvariable=self.symbol, width=18)
        self.combo.grid(row=0, column=1, **pad)
        tk.Button(win, text="Detect from MT5", command=self.detect).grid(
            row=0, column=2, **pad)

        tk.Label(win, text="Lot per order").grid(
            row=1, column=0, sticky="w", **pad)
        tk.Entry(win, textvariable=self.lot, width=10).grid(
            row=1, column=1, sticky="w", **pad)

        tk.Label(win, text="Max layers bought/sold at market per side").grid(
            row=2, column=0, sticky="w", **pad)
        ttk.Spinbox(win, from_=0, to=12, textvariable=self.cap, width=6).grid(
            row=2, column=1, sticky="w", **pad)

        ttk.Separator(win).grid(row=3, column=0, columnspan=3,
                                sticky="ew", pady=6)
        tk.Checkbutton(
            win, text="Auto cut loss (close a side when its Cut Loss holds)",
            variable=self.auto, command=self.sync,
        ).grid(row=4, column=0, columnspan=3, sticky="w", **pad)

        tk.Label(win, text="      Hold time before closing (seconds)").grid(
            row=5, column=0, sticky="w", **pad)
        self.hold_box = ttk.Spinbox(win, from_=10, to=3600,
                                    textvariable=self.hold, width=8)
        self.hold_box.grid(row=5, column=1, sticky="w", **pad)

        self.scl_check = tk.Checkbutton(
            win, text="      Stop Cut Loss (SCL): price must also reach it",
            variable=self.scl, command=self.sync,
        )
        self.scl_check.grid(row=6, column=0, columnspan=3, sticky="w", **pad)
        tk.Label(win, text="      SCL distance (% of the way, Cut Loss to SL)"
                 ).grid(row=7, column=0, sticky="w", **pad)
        self.pct_box = ttk.Spinbox(win, from_=5, to=95,
                                   textvariable=self.pct, width=8)
        self.pct_box.grid(row=7, column=1, sticky="w", **pad)

        tk.Label(
            win, justify="left", fg=GREY, wraplength=430,
            text="Changes apply after ACTP restarts. With auto cut loss OFF, "
                 "nothing closes a side automatically: only the broker's "
                 "SL/TP, KILL ALL, or you.",
        ).grid(row=8, column=0, columnspan=3, sticky="w", **pad)

        row = tk.Frame(win)
        row.grid(row=9, column=0, columnspan=3, sticky="e", **pad)
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

    def sync(self) -> None:
        on = self.auto.get()
        self.hold_box.configure(state="normal" if on else "disabled")
        self.scl_check.configure(state="normal" if on else "disabled")
        self.pct_box.configure(
            state="normal" if on and self.scl.get() else "disabled")

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

    def save(self) -> None:
        try:
            raw = {
                "symbol": self.symbol.get(),
                "lot_per_order": self.lot.get(),
                "auto_cut_loss": self.auto.get(),
                "cut_loss_hold_seconds": int(self.hold.get()),
                "scl_enabled": self.scl.get(),
                "scl_fraction": str(Decimal(self.pct.get()) / 100),
                "max_market_layers": int(self.cap.get()),
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
        banner = "LIVE - REAL MONEY"
    elif is_real:
        banner = f"LIVE - REAL MONEY - {account.server}"
    else:
        banner = f"DEMO ACCOUNT - {account.server}"
    return (
        ExecutionManager(broker, JsonPlanStore(STATE_PATH), check_stale=True),
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
        self.auto_tp = tk.BooleanVar(value=False)
        self._tp_last: dict[str, str] = {}
        self.monitor = CutLossMonitor(manager) if config.AUTO_CUT_LOSS else None
        self._last_ok: datetime | None = None
        self._chart = ChartLevelsWriter(config.CHART_LEVELS_PATH)
        self._chart_err_logged: str | None = None

        root.title("ACTP - Auto Copy Trade Plan")
        root.geometry("680x640")
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        tk.Label(
            root, text=banner, fg="white", bg=RED if is_real else GREEN,
            font=("TkDefaultFont", 10, "bold"),
        ).pack(fill="x")
        tk.Label(root, text="Paste message:").pack(
            anchor="w", padx=8, pady=(8, 0))
        self.box = scrolledtext.ScrolledText(root, height=10)
        self.box.pack(fill="both", expand=True, padx=8)

        buttons = tk.Frame(root)
        buttons.pack(fill="x", padx=8, pady=6)
        tk.Button(buttons, text="Send", width=12, command=self.send).pack(
            side="left")
        tk.Button(buttons, text="Clear", width=8, command=self.clear).pack(
            side="left", padx=6)
        tk.Button(buttons, text="Settings...", command=self.open_settings
                  ).pack(side="left")
        tk.Button(buttons, text="Reconcile (clear halt)",
                  command=self.reconcile).pack(side="right")
        tk.Button(buttons, text="KILL ALL", fg="white", bg=RED, width=10,
                  command=self.kill).pack(side="right", padx=6)

        row2 = tk.Frame(root)
        row2.pack(fill="x", padx=8)
        tk.Button(row2, text="Close all positions",
                  command=lambda: self.close_positions_ask(False)
                  ).pack(side="left")
        tk.Button(row2, text="Close profitable only",
                  command=lambda: self.close_positions_ask(True)
                  ).pack(side="left", padx=6)

        tp_row = tk.Frame(root)
        tp_row.pack(fill="x", padx=8)
        tk.Checkbutton(
            tp_row,
            text="Auto-TP: when all layers of a side fill, move TP to the "
                 "first entry",
            variable=self.auto_tp,
        ).pack(side="left")

        self.status = tk.Label(root, text="Status: running", anchor="w")
        self.status.pack(fill="x", padx=8)
        self.monitor_label = tk.Label(root, text="", anchor="w")
        self.monitor_label.pack(fill="x", padx=8)

        tk.Label(root, text="Log:").pack(anchor="w", padx=8)
        self.log_box = scrolledtext.ScrolledText(
            root, height=10, state="disabled")
        self.log_box.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.startup_check()
        self.log(
            f"STARTED: {banner}. Symbol {config.SYMBOL}, "
            f"lot {config.LOT_PER_ORDER}, auto cut loss "
            + (f"ON ({config.CUT_LOSS_HOLD_SECONDS}s, SCL "
               f"{'ON' if config.SCL_ENABLED else 'OFF'})"
               if config.AUTO_CUT_LOSS else "OFF")
        )
        if self.monitor is None:
            self.log("WARN: auto cut loss is OFF. Positions are protected "
                     "only by their broker SL/TP.")
        root.after(POLL_MS, self.poll)
        self.refresh_status()

    # ---- helpers ---------------------------------------------------------
    def log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{stamp}] {text}\n")
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
                text=f"Status: HALTED - {self.manager.halt_reason}", fg=RED)
        else:
            self.status.configure(text="Status: running", fg="black")
        if self.monitor is None:
            self.monitor_label.configure(
                text="Auto cut loss: OFF (only broker SL/TP protect positions)",
                fg=RED)
        elif self.monitor.error:
            self.monitor_label.configure(
                text=f"Cut-loss monitor: PROBLEM - {self.monitor.error}",
                fg=RED)
        elif self._last_ok is None:
            self.monitor_label.configure(
                text="Cut-loss monitor: starting...", fg="black")
        else:
            self.monitor_label.configure(
                text="Cut-loss monitor: OK, last check "
                + self._last_ok.strftime("%H:%M:%S"), fg=GREEN)

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
            f"Startup: {len(result.active_orders)} active ACTP orders"
            + (" (MIXED PLANS - check!)" if result.mixed_plans else "") + "."
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
        text = self.box.get("1.0", "end").strip()
        if not text:
            return
        try:
            preview = self.dispatcher.preview(text)
        except (ValueError, ExecutionHalted) as exc:
            self.log(f"NOT SENT: {exc}")
            self.refresh_status()
            return
        self.file_log.info("PREVIEW: %s", preview.replace("\n", " | "))
        if not messagebox.askokcancel(
            "Confirm", f"{preview}\n\nSend this to your account?"
        ):
            self.log("Cancelled.")
            return
        self.log("Confirmed by user.")
        try:
            result = self.dispatcher.dispatch(text)
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
            self.clear()
        self.refresh_status()

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
                f"Reconciled. {len(result.active_orders)} active orders"
                + (" (MIXED PLANS - check!)" if result.mixed_plans else "") + "."
            )
        self.refresh_status()

    def kill(self) -> None:
        if not messagebox.askokcancel(
            "KILL ALL",
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
        if not messagebox.askokcancel(
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
        if not messagebox.askokcancel(
            "Quit",
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
    App(root, manager, broker, banner, is_real)
    root.mainloop()


if __name__ == "__main__":
    main()