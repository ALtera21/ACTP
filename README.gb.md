🇮🇩 [Bahasa Indonesia](README.md)

# ACTP – Auto Copy Trade Plan

ACTP places a layered set of MT5 pending orders from a trade plan you paste in, and manages them afterwards (Cut Loss, close buttons, Auto-TP). It never reads WhatsApp for you: you copy and paste each message.

> [!WARNING]
> ACTP places **real orders** on the MetaTrader 5 account that is logged in and can lose money. It comes with **NO WARRANTY** (see the LICENSE file). It is not financial advice and is not affiliated with any signal provider, WhatsApp, MetaQuotes or any broker. You are responsible for every order. Test on a demo account first.

## 🎬 Video tutorial

<p align="center">
  <a href="https://youtu.be/d0bM5Vmml2w">
    <img src="docs\ACTP tutorial.png" width="500" alt="ACTP tutorial video">
  </a>
</p>


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

**[CLICK HERE TO DOWNLOAD ACTP – LATEST RELEASE](https://github.com/ALtera21/ACTP/releases/download/v1.1.0/dist.zip)**

1. Download the provided `.zip` file under **Assets**.
2. Extract the ZIP file to your preferred directory.
3. Run `ACTP.exe` from the extracted folder. Keep it together with the `_internal` folder; do not move the exe elsewhere or run it from inside the ZIP.

> **Note:** Windows SmartScreen may warn about an unsigned app: “More info → Run anyway”. PyInstaller exes also trigger antivirus false positives.

Example:

```text
C:\ACTP
```

**Verify your download (v1.1.0):** in PowerShell run
```powershell
Get-FileHash .\dist.zip -Algorithm SHA256
```
and compare with: `6b106a8917b86770f9ac3955bbe26cf7a21a169a882cd6383a269bd817f5a16c`

## 🚀 ACTP setup

1. Open MetaTrader 5, log in, and switch **Algo Trading** ON.
2. Double-click `ACTP.exe`.
3. **First start only:** read the risk notice and click OK. The Settings window opens:
   - Pick your broker's XAUUSD symbol (**Detect from MT5** fills the list).
   - Set the lot per order.
   - Choose your Cut Loss options (see below).
   - Click **Save**. ACTP restarts by itself.
4. The window banner shows which account you're on: **red = REAL MONEY**, **green = DEMO**. Try a demo account first.
5. Copy a trade plan from WhatsApp, paste it into the box, click **Send**, read the summary, and confirm.

Your settings, logs and saved plans are in `%APPDATA%\ACTP` (`settings.json`, `logs\`, `state\`). To uninstall, delete the ACTP folder and `%APPDATA%\ACTP`.

**Important:** settings can only be saved while MT5 is open, because ACTP checks the symbol and lot against your broker.

## 🖥️ Using ACTP

### Messages ACTP understands

| Message | What ACTP does |
|---|---|
| `PLAN XAUUSDr` with BUY LIMIT and/or SELL LIMIT sections | Replaces the current plan on those sides and places one pending order per layer. |
| `UPDATE XAUUSDr BUY\|SELL` with `SL: a ➜ b` and/or `TP: a ➜ b` | Changes SL/TP on that side's orders. |
| `CLOSE XAUUSDr BUY\|SELL` | Closes the **open positions** of that side. Pending orders of that side stay. |
| `XAUUSDr BUY\|SELL - Entry ter-fill` | Noted only. Nothing is sent. |

Limits: 2 to 20 layers per side, at most 12 orders per plan, prices with 2 decimals. Pasting the identical text twice within 30 seconds is refused.

### Window controls

| Control | What it does |
|---|---|
| **Paste box** | Where you paste the message. |
| **MultiLayer** box | Optional. Sets the lot of each layer and the number of layers for the plan you send next (see "MultiLayer" below). Blank = the plan's own layer count, one base lot each. |
| **Send** | Parses the message, shows a summary (including how many layers would fill at market right now), and sends it after you confirm. |
| **Clear** | Empties the paste box. |
| **Settings...** | Opens the settings window. Changes apply after a restart (ACTP offers one). |
| **Reconcile (clear halt)** | After a failed operation ACTP stops (status shows HALTED). Check MT5, press this to re-read it, then Send the same message again to resume. (Think of this as a refresh button)|
| **KILL ALL** | Cancels every pending ACTP order and closes every open ACTP position. Asks to confirm. |
| **Close all positions** | Closes open positions only. Pending orders stay. |
| **Close profitable only** | Closes only the positions currently in profit. Spread is counted; swap and commission are not. |
| **Cancel buy orders** / **Cancel sell orders** | Cancels the **pending** orders of that side only. Open positions stay open and keep their SL/TP and Cut Loss protection. Asks to confirm and shows how many orders will be cancelled. Does not run while HALTED: press Reconcile first. |
| **Auto-TP** checkbox | When every layer of a side is filled, moves that side's TP to its first entry a.k.a the `proximal` (BUY: the highest layer, SELL: the lowest). Once per plan and side. Starts OFF every time ACTP starts. |

> [!NOTE]
> ACTP only touches orders it placed. Your own manual trades are never modified.

<p align="center">
  <img src="docs\main-screen.png" width="400" alt="Project Dashboard">
</p>

<p align="center">
  <img src="docs\halted.png" width="400" alt="Error halted, press reconcile button">
</p>

### MultiLayer

The **MultiLayer** box under the paste box lets you choose the size of every layer, and how many layers there are, for the next plan you send. Type one number per layer, for example `1, 1, 1, 1, 2, 2`. Each number multiplies the **Lot per order** from Settings (the base lot).

| Base lot | MultiLayer | Result |
|---|---|---|
| 0.06 | `1, 1, 1, 1, 2, 2` | 6 layers: 0.06 x 4, then 0.12 x 2 |
| 0.06 | `1, 1, 1, 2` | 4 layers: 0.06 x 3, then 0.12 x 1 |

<p align="center">
  <img src="docs/multilayer.gif" width="500" alt="Typing a MultiLayer list and seeing the lot of each layer">
</p>

- **Layer 1 is the first price the market reaches** (BUY: the highest entry, SELL: the lowest entry). The larger lots in the examples above are the deepest layers.
- The list sets the layer count too. ACTP spreads that many layers evenly across the plan's area, whatever number of layers the message says.
- **Leave the box blank** to work as before: the message's layer count, one base lot each.
- The same list is used for BUY and SELL. It is ignored by UPDATE and CLOSE messages.
- Use 2 to 12 numbers, each above 0 and at most 10. Decimals such as `1.5` are fine if the resulting lot is a multiple of 0.01. The total of 12 orders per plan still applies, so a list of more than 6 numbers only works on a one-sided plan.
- The Send summary lists the lot of every layer and the total per side. ACTP also checks the lots against your broker's minimum, step and maximum before anything is sent.
- The box is **not saved** and is **not cleared after Send**. While it is filled, an orange line below it shows the lots that will be used.
- If a plan is interrupted (HALTED), Send the same message again with the **same** MultiLayer value to resume it. A different value starts a new plan.
- **Max layers at market per side** counts layers, not lots, so a larger layer counts the same as a small one.

### Late plans

If you paste a plan after price has already moved into it, layers whose entry is already reached are bought **at market** instead of failing. The Send summary shows the split. The plan is refused when:
- more layers than **Max layers at market per side** would fill at market, or
- price is already through that side's Cut Loss or SL.

### Cut Loss

With **Auto cut loss** on, ACTP closes a whole side (cancels pending, closes open) when:
1. price (BUY: bid, SELL: ask) stays beyond the plan's Cut Loss for the **hold time** without interruption, **and**
2. with **SCL** on, price is also at or beyond the Stop Cut Loss line at that moment.

SCL sits between the Cut Loss and the SL, at the chosen percentage of the way from Cut Loss to SL. A gap of more than 15 seconds between price checks restarts the timer. With auto cut loss **off**, nothing closes a side automatically: only the broker's SL/TP, KILL ALL, or you.

**Cut Loss and Auto-TP only run while the ACTP window is open.** Closing it asks for confirmation, because monitoring stops. Orders keep their SL/TP at the broker.

## 📋 Trading Configuration

Set these in **Settings...**. Defaults are shown.

| Setting | Default | Allowed | Meaning |
|---|---|---|---|
| XAUUSD symbol | `XAUUSDc` | Any XAUUSD variant (`XAUUSD`, `XAUUSDm`, `XAUUSD.r`, ...) | The only symbol ACTP will trade. Messages always say `XAUUSDr`; that is the provider's name, not yours. |
| Lot per order | 0.01 | 0.01 to 10, steps of 0.01 | Volume of each layer (the base lot that MultiLayer multiplies). Must fit your broker's limits. |
| Max layers at market per side | 3 | 0 to 12 | See "Late plans". |
| Auto cut loss | On | On / Off | See "Cut Loss". |
| Hold time | 60 s | 10 to 3600 | How long a Cut Loss breach must last. |
| SCL | On | On / Off | Price must also reach the Stop Cut Loss line. |
| SCL distance | 15 % | 5 to 95 | How far from Cut Loss toward SL the SCL line sits. |

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
| `Status: HALTED` | Check MT5, press **Reconcile**, then Send the same message again to resume. |
| Windows blocks the app | More info → Run anyway (see Download). |
| This account is not a HEDGING account | Use a hedging account. Ask your broker.|

**Logs:** ACTP writes one log file per day to `%APPDATA%\ACTP\logs\actp.log` (30 days kept). Paste `%APPDATA%\ACTP\logs` into the File Explorer address bar to open the folder. If you report a problem, attach the relevant lines. They contain your broker server name, order tickets and prices, so look them over before posting publicly.

Bugs and questions: open an issue on the GitHub repository.

## 📄 License

See the repository's LICENSE file for licensing and usage conditions.