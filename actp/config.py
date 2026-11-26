import os
import sys
from decimal import Decimal
from pathlib import Path

from actp import settings as _settings


def _app_dir() -> Path:
    """Where settings, state and logs live. Dev: the folder you run from.
    Packaged app: %APPDATA%\\ACTP."""
    override = os.environ.get("ACTP_HOME")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(os.environ.get("APPDATA") or Path.home()) / "ACTP"
    return Path.cwd()


APP_DIR = _app_dir()
SETTINGS_PATH = APP_DIR / "settings.json"
FIRST_RUN = not SETTINGS_PATH.exists()
SETTINGS_ERROR: str | None = None
try:
    _v = _settings.load(SETTINGS_PATH)
except ValueError as _exc:
    SETTINGS_ERROR = str(_exc)  # app.py refuses to start and shows this
    _v = _settings.validate({})

# ---- Editable in the settings window (stored in settings.json) ----------
SYMBOL = _v["symbol"]                       # your broker's XAUUSD symbol
LOT_PER_ORDER = Decimal(_v["lot_per_order"])
# Size of one pip in price units, for the pips shown in the re-entry window
# (XAUUSD: 1 pip = 0.10). Change it here if you count pips differently.
PIP_SIZE = Decimal("0.1")
AUTO_CUT_LOSS = _v["auto_cut_loss"]
CUT_LOSS_HOLD_SECONDS = _v["cut_loss_hold_seconds"]
SCL_ENABLED = _v["scl_enabled"]
SCL_FRACTION = Decimal(_v["scl_fraction"])  # how far from Cut Loss to SL
MAX_MARKET_LAYERS_PER_SIDE = _v["max_market_layers"]
CUT_LOSS_MODE = _v["cut_loss_mode"]
CUT_LOSS_TIMEFRAME = _v["cut_loss_timeframe"]
STOP_LOSS_MULTIPLE = Decimal(_v["stop_loss_multiple"])
LAYER_AMOUNT = _v["layer_amount"]
SAVED_MULTILAYERS: dict[str, str] = dict(_v["saved_multilayers"])
CONFIRM: dict[str, bool] = dict(_v["confirm"])
LANGUAGE = _v["language"]
THEME = _v["theme"]
AUTO_TP = _v["auto_tp"]
TP_INCLUDE_PENDING = _v["tp_include_pending"]
AUTO_TP_PERCENT = Decimal(_v["auto_tp_percent"])
AUTO_TP_PRIORITY = _v["auto_tp_priority"]
AUTO_REMOVE = _v["auto_remove"]
AUTO_REMOVE_PERCENT = Decimal(_v["auto_remove_percent"])

# ---- Fixed ---------------------------------------------------------------
# The symbol the signal provider writes in its messages.
SOURCE_SYMBOL = "XAUUSDr"

# If price checks are further apart than this, the breach was not observed
# continuously and its timer restarts.
MAX_POLL_GAP_SECONDS = 15

# The Cut Loss / SCL chart lines. ActpLevels.mq5 reads this file from MT5's
# Common\\Files folder. Set to "" to turn the chart lines off.
CHART_LEVELS_PATH = ""