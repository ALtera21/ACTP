🇮🇩 [Bahasa Indonesia](README.md)

# ACTP – Auto Copy Trade Plan

<p align="center">
    <img src="docs\mylogo.png" width="256" alt="icon">
</p>

ACTP places a layered set of MT5 pending orders from a trade plan you paste in (or fill in by hand on the **Manual Input** tab), and manages every plan afterwards (Cut Loss, close buttons, Auto-TP). It never reads WhatsApp for you: you copy and paste each message.

> [!WARNING]
> ACTP places **real orders** on the MetaTrader 5 account that is logged in and can lose money. It comes with **NO WARRANTY** (see the LICENSE file). It is not financial advice and is not affiliated with any signal provider, WhatsApp, MetaQuotes or any broker. You are responsible for every order. Test on a demo account first.

## 🎬 Video tutorial

<p align="center">
  <a href="https://youtu.be/d0bM5Vmml2w">
    <img src="docs\ACTP tutorial.png" width="500" alt="ACTP tutorial video">
  </a>
</p>

> The video shows v1.0.2 See [CHANGELOG.md](CHANGELOG.md) for what is new in v2.0.0.

## 💻 Requirements

Before installing ACTP, make sure you have:

- Windows 10 or Windows 11 (64-bit).
- MetaTrader 5 desktop terminal.
- A hedging MT5 account. On a netting account the layers of one side merge into a single position. ACTP refuses to start on a netting account.
- Algo Trading switched ON in the MT5 toolbar.
- MT5 must be open and logged in whenever ACTP runs.
- Your broker offers an XAUUSD symbol (XAUUSD, XAUUSDc, XAUUSDm, XAUUSD.r and so on).

## 📥 Download

Download the latest ACTP release from the official GitHub Releases page:

**[CLICK HERE TO DOWNLOAD ACTP – LATEST RELEASE](https://github.com/ALtera21/ACTP/releases/download/v2.0.1/dist.zip)**

1. Download the provided `.zip` file under **Assets**.
2. Extract the ZIP file to your preferred directory.
3. Run `ACTP.exe` from the extracted folder. Keep it together with the `_internal` folder; do not move the exe elsewhere or run it from inside the ZIP.

> **Note:** Windows SmartScreen may warn about an unsigned app: “More info → Run anyway”. PyInstaller exes also trigger antivirus false positives.

Example:

```text
C:\ACTP
```

**Verify your download (v2.0.0):** in PowerShell run
```powershell
Get-FileHash .\dist.zip -Algorithm SHA256
```
and compare with: `1384356ed0aff73e6c2ab62ba8bf042caf2b6e7edfb41b4964e45bc1b79e8361`

**Updating from v1.1.0:** close ACTP, extract v2.0.0 into a new folder and run it. Your settings and saved state in `%APPDATA%\ACTP` carry over. Plans still open from v1.1.0 keep their old hold-timer Cut Loss.

## 🚀 ACTP setup

1. Open MetaTrader 5, log in, and switch **Algo Trading** ON.
2. Double-click `ACTP.exe`.
3. **First start only:** read the risk notice and click OK. The Settings window opens:
   - Pick your broker's XAUUSD symbol (**Detect from MT5** fills the list).
   - Choose your Cut Loss options (see below).
   - Tick **Create a desktop shortcut** if you want one.
   - Click **Save**. ACTP restarts by itself.
4. The window banner shows which account you're on: **dark blue = LIVE (real money)**, **green = DEMO**, with the server name. Try a demo account first.
5. Set the **Lot** and **Candle** timeframe on the main window, copy a trade plan from WhatsApp, paste it into the box, click **Send**, read the summary, and confirm.

Your settings, logs and saved plans are in `%APPDATA%\ACTP` (`settings.json`, `logs\`, `state\`). To uninstall, delete the ACTP folder and `%APPDATA%\ACTP`.

**Important:** settings can only be saved while MT5 is open, because ACTP checks the symbol and lot against your broker.

## 🖥️ Using ACTP

### Messages ACTP understands

| Message | What ACTP does |
|---|---|
| `PLAN XAUUSDr` with BUY LIMIT and/or SELL LIMIT sections | Starts a **new plan** and places one pending order per layer. Older plans are **not** replaced; each plan lives on its own. |
| `UPDATE XAUUSDr BUY\|SELL` with `SL: a ➜ b` and/or `TP: a ➜ b` | Changes SL/TP on that side of the **most recent** plan that has it. |
| `CLOSE XAUUSDr BUY\|SELL` | Closes the **open positions** of that side in every plan. Pending orders of that side stay. |
| `XAUUSDr BUY\|SELL - Entry ter-fill` | Noted only. Nothing is sent. |

Limits: 2 to 200 layers per side, at most 400 orders per plan, prices with 2 decimals. Pasting the identical text twice within 30 seconds is refused.

### Window controls

| Control | What it does |
|---|---|
| **Paste message** tab | Where you paste the message. |
| **Manual Input** tab | Build a plan by hand (see "Manual Input" below). |
| **MultiLayer** box | Optional. Sets the lot multiplier of each layer and the number of layers for the plan you send next (see "MultiLayer" below). |
| **Layer amount** | How many layers the next plan gets when MultiLayer is empty (2 to 200). |
| **Lot** | Base lot of each order. A change applies immediately and is saved. It is checked against your broker's minimum, step and maximum. |
| **Candle** | Candle timeframe of the Cut Loss for plans you send from now on (M1 to H1). Plans already sent keep theirs. Greyed out when auto cut loss is off or the legacy timer is used. |
| ☾ / ☀ | Switches between the light and dark theme (applies after a restart). |
| **Send** | Parses the message, shows a summary (including how many layers would fill at market right now), and sends it after you confirm. |
| **Clear** | Empties the paste box. |
| **Settings...** | Opens the settings window. Changes apply after a restart (ACTP offers one). |
| **Reconcile (clear halt)** | After a failed operation ACTP stops (status shows HALTED). Check MT5, press this to re-read it, then Send the same message again to resume. (Think of this as a refresh button) |
| **KILL ALL** | Cancels every pending ACTP order and closes every open ACTP position, in every plan. |
| **Close all positions** | Closes open positions only. Pending orders stay. |
| **Close profitable only** | Closes only the positions currently in profit. Spread is counted; swap and commission are not. |
| **Cancel buy orders** / **Cancel sell orders** | Cancels the **pending** orders of that side in all plans. Open positions stay open and keep their SL/TP and Cut Loss protection. Does not run while HALTED: press Reconcile first. |
| **Auto-TP** checkbox | When every layer of a side of a plan is filled, moves that plan's TP on that side to its first entry a.k.a. the `proximal` (BUY: the highest layer, SELL: the lowest). Once per plan and side. Starts OFF every time ACTP starts. |

Most of these ask you to confirm first; see "Popup confirmation" below.

> [!NOTE]
> ACTP only touches orders it placed. Your own manual trades are never modified.

<p align="center">
  <img src="docs\main-screen.png" width="400" alt="Project Dashboard">
</p>

<p align="center">
  <img src="docs\halted.png" width="400" alt="Error halted, press reconcile button">
</p>

### Live plans

Every plan you send gets a name: **Alpha, Beta, Gamma … Omega** (then a short random id). The **Live plans** table shows one row per plan with its time sent, BUY and SELL orders (filled / pending, Cut Loss) and its candle countdown. The same name is in the MT5 order comment, for example `1763440000-Alpha:3` (time sent, plan name, layer number).

Each row has small buttons (hover to read what they do):

| Button | What it does |
|---|---|
| green **✕** beside BUY | Cancels that plan's pending BUY orders and closes its open BUY positions. |
| red **✕** beside SELL | The same for SELL. |
| dark **✕** at the end of the row | Cancels the entire plan and closes all its positions. |

Other plans are never touched. These buttons keep working while ACTP is HALTED.

The **timer box** next to the status lines shows the timeframe in use, the time left until the candle closes and the plan name. The number goes from green to yellow to red as the candle is about to close. It also runs while no plan is live (then it shows the timeframe the next plan will use).

### Manual Input

The **Manual Input** tab lets you build a plan without a WhatsApp message. Fill in the **range** (the two prices) of the side or sides you want, SELL on top and BUY below; a side with an empty range is left out. Leave **Cut loss**, **Stop loss** or **Take profit** empty to use the default shown next to the box:

| Price | Default |
|---|---|
| Cut loss | 30% of the range width beyond the far end of the range |
| Stop loss | **Manual SL = x range** (Settings, default 3) times the range width beyond the far end |
| Take profit | One range width past the top layer |

Layers and lots come from the **Layer amount** and **MultiLayer** boxes. The tab writes a normal `PLAN` text and sends it through the same checks as a pasted message, so you get the same summary and confirm popup.

<p align="center">
  <img src="docs\main-manual-screen.png" width="400" alt="Project Dashboard">
</p>

### MultiLayer

The **MultiLayer** box under the paste box lets you choose the size of every layer, and how many layers there are, for the next plan you send. Type one number per layer, for example `1, 1, 1, 1, 2, 2`. Each number multiplies the **Lot** (the base lot).

| Base lot | MultiLayer | Result |
|---|---|---|
| 0.06 | `1, 1, 1, 1, 2, 2` | 6 layers: 0.06 x 4, then 0.12 x 2 |
| 0.06 | `1, 1, 1, 2` | 4 layers: 0.06 x 3, then 0.12 x 1 |

<p align="center">
  <img src="docs/multilayer.gif" width="500" alt="Typing a MultiLayer list and seeing the lot of each layer">
</p>

> The GIF shows v1.0.2 See [CHANGELOG.md](CHANGELOG.md) for what is new in v2.0.0.

- **Layer 1 is the first price the market reaches** (BUY: the highest entry, SELL: the lowest entry). The larger lots in the examples above are the deepest layers.
- The list sets the layer count too. ACTP spreads that many layers evenly across the plan's area, whatever number of layers the message says.
- **Leave the box blank** and ACTP uses the **Layer amount** box: that many layers, one base lot each (or your **Saved MultiLayer** for that amount, see below).
- The same list is used for BUY and SELL. It is ignored by UPDATE and CLOSE messages.
- Use 2 to 200 numbers, each above 0 and at most 10. Decimals such as `1.5` are fine if the resulting lot is a multiple of 0.01. A plan can hold at most 400 orders in total.
- The Send summary lists the lot of every layer and the total per side. ACTP also checks the lots against your broker's minimum, step and maximum before anything is sent.
- The box is **not saved** and is **not cleared after Send**. While it is filled, an orange line below it shows the lots that will be used.
- If a plan is interrupted (HALTED), Send the same message again with the **same** MultiLayer value to resume it. A different value starts a new plan.
- **Max layers at market per side** counts layers, not lots, so a larger layer counts the same as a small one.

**Saved MultiLayer** (Settings → **Saved MultiLayer...**): save a list for a layer amount, for example 4 layers = `1, 1, 1, 2`. Whenever the MultiLayer box is empty and the Layer amount is 4, that list is used. A layer amount with no saved list uses 1x for every layer. A list typed in the MultiLayer box always wins.

### Late plans

If you paste a plan after price has already moved into it, layers whose entry is already reached are bought **at market** instead of failing. The Send summary shows the split. The plan is refused when:
- more layers than **Max layers at market per side** would fill at market, or
- price is already through that side's Cut Loss or SL.

### Cut Loss

With **Auto cut loss** on, ACTP closes a whole side of a plan (cancels pending, closes open) as soon as the rule below is met. Each plan keeps the rule and timeframe it was sent with, so changing the settings later does not change plans that are already live.

**Candle close (default).** A candle of your chosen timeframe must **close** beyond the plan's Cut Loss (BUY: below it, SELL: above it). Only candles that opened **after** the plan was sent count, and a price spike that comes back before the candle closes does nothing. Pick the timeframe in the **Candle** box (M1, M2, M3, M4, M5, M6, M10, M12, M15, M20, M30, H1). The timer box counts down to the close of the running candle. The countdown follows the last price tick, so it can be a few seconds off in a quiet or closed market.

**Legacy timer (optional).** Tick **Use the legacy timer instead** in Settings to go back to the v1 rule:
1. price (BUY: bid, SELL: ask) stays beyond the plan's Cut Loss for the **hold time** without interruption, **and**
2. with **SCL** on, price is also at or beyond the Stop Cut Loss line at that moment.

SCL sits between the Cut Loss and the SL, at the chosen percentage of the way from Cut Loss to SL. A gap of more than 15 seconds between price checks restarts the timer.

With auto cut loss **off**, nothing closes a side automatically: only the broker's SL/TP, KILL ALL, or you.

**Cut Loss and Auto-TP only run while the ACTP window is open.** Closing it asks for confirmation, because monitoring stops. Orders keep their SL/TP at the broker.

### Popup confirmation

Settings has a red **Popup configurations (Dangerous settings)** box. **Popup confirmation** is the master switch and **Configure each popup...** opens one checkbox per action: Send plan, Cancel PO buy / sell, KILL ALL, Close all positions, Close profitable only, the plan-table buttons and Quit. They are all ticked by default, which means a normal OK / Cancel popup.

**Unticking** a box (or the master box) first asks you to type a random 5-digit code. After that, the action runs immediately with **no popup at all**, so only do this if you accept the risk of an accidental click. Ticking a box again needs no code.

### Language and theme

- **Language:** Settings → **Language**: English or Bahasa Indonesia. Applies after a restart. Detailed error texts from the broker and the parser stay in English.
- **Theme:** the ☾ / ☀ button on the right of the Layer amount row switches light and dark. Applies after a restart.

### Logo

The logo in the title bar and taskbar is set in `logo.py` (a picture file or an emoji). To also change the icon of the `.exe`, see `make_icon.py` (build instructions are in its first lines).

## 📋 Trading Configuration

Set these in **Settings...** (or on the main window where noted). Defaults are shown.

| Setting | Default | Allowed | Meaning |
|---|---|---|---|
| XAUUSD symbol | `XAUUSDc` | Any XAUUSD variant (`XAUUSD`, `XAUUSDm`, `XAUUSD.r`, ...) | The only symbol ACTP will trade. Messages always say `XAUUSDr`; that is the provider's name, not yours. |
| Lot (main window) | 0.01 | 0.01 to 10, steps of 0.01 | Volume of each layer (the base lot that MultiLayer multiplies). Must fit your broker's limits. |
| Layer amount (main window) | 6 | 2 to 200 | Number of layers of the next plan when MultiLayer is empty. |
| Candle (main window) | M1 | M1 to M6, M10, M12, M15, M20, M30, H1 | Candle timeframe of the Cut Loss for new plans. |
| Max layers at market per side | 3 | 0 to 200 | See "Late plans". |
| Manual SL = x range | 3 | 0.1 to 100 | Default Stop loss distance of the Manual Input tab. |
| Auto cut loss | On | On / Off | See "Cut Loss". |
| Use the legacy timer | Off | On / Off | Use the hold-timer + SCL rule instead of candles. |
| Hold time | 60 s | 10 to 3600 | Legacy timer only: how long a Cut Loss breach must last. |
| SCL | On | On / Off | Legacy timer only: price must also reach the Stop Cut Loss line. |
| SCL distance | 15 % | 5 to 95 | Legacy timer only: how far from Cut Loss toward SL the SCL line sits. |
| Popup confirmation | All On | per action | See "Popup confirmation". |
| Language | English | English / Bahasa Indonesia | Window language. |

Your symbol must have at least 2 price decimals.

<p align="center">
  <img src="docs\settings.png" width="400" alt="Error halted, press reconcile button">
</p>

> **Note:**
The lot size advertised in the original WhatsApp trading plan is ignored. ACTP uses its own configured volume.

## ⚠️ Important Notes

- ACTP has no dry-run mode. Test on a demo account first, one copy per account, keep PC awake and window open, check banner and server, risks are yours.
- Ensure your MT5 terminal is connected to the correct trading account.
- Never use an unverified release for live trading.
- Trading involves financial risk. You are responsible for configuring and monitoring your trading environment.

## 🐛 Troubleshooting

| Message | What to do |
|---|---|
| `Cannot start: ...` | Open MetaTrader 5, log in, turn on Algo Trading, then start ACTP again. |
| `Cannot read your settings file` | Fix or delete `%APPDATA%\ACTP\settings.json`. |
| `Status: HALTED` | Check MT5, press **Reconcile**, then Send the same message again to resume. To get rid of one plan, use its dark **✕** button in the Live plans table. |
| Windows blocks the app | More info → Run anyway (see Download). |
| This account is not a HEDGING account | Use a hedging account. Ask your broker. |
| The logo does not change | Restart ACTP. An emoji logo needs Pillow; use a PNG file instead (see `logo.py`). |

**Logs:** ACTP writes one log file per day to `%APPDATA%\ACTP\logs\actp.log` (30 days kept). Paste `%APPDATA%\ACTP\logs` into the File Explorer address bar to open the folder. If you report a problem, attach the relevant lines. They contain your broker server name, order tickets and prices, so look them over before posting publicly.

Bugs and questions: open an issue on the GitHub repository.

## 📄 License

See the repository's LICENSE file for licensing and usage conditions.
