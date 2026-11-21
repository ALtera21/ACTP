"""Language of the window: English (default) or Bahasa Indonesia.

English text stays in the code. For Indonesian, `tr()` looks the English text
up here; a text with no entry is shown in English. Entries may hold {name}
placeholders, which match any text (numbers, side names, error texts) and are
put into the Indonesian text; the matched parts are translated too.

Only what the window itself shows is translated. Detailed error texts that come
from the broker, the parser or the validator stay in English.
"""

from __future__ import annotations

import re
from typing import Any

LANGUAGES = {"en": "English", "id": "Bahasa Indonesia"}
DEFAULT_LANGUAGE = "en"

ID: dict[str, str] = {
    # ---- main window ----
    "Paste message": "Tempel pesan",
    "Manual Input": "Input Manual",
    "MultiLayer:": "MultiLayer:",
    "Layer amount:": "Jumlah layer:",
    "(MultiLayer box wins when filled)": "(Kotak MultiLayer diutamakan jika diisi)",
    "Send": "Kirim",
    "Clear": "Hapus",
    "Settings...": "Pengaturan...",
    "Reconcile (clear halt)": "Rekonsiliasi (hapus henti)",
    "KILL ALL": "HENTIKAN SEMUA",
    "Close all positions": "Tutup semua posisi",
    "Close profitable only": "Tutup yang profit saja",
    "Cancel sell orders": "Batalkan order sell",
    "Cancel buy orders": "Batalkan order buy",
    "Auto-TP: when all layers of a side fill, move TP to the first entry":
        "Auto-TP: saat semua layer satu sisi terisi, pindahkan TP ke entry pertama",
    "Status: running": "Status: berjalan",
    "Status: HALTED - {reason}": "Status: BERHENTI - {reason}",
    "Live plans (each plan has its own Cut Loss):":
        "Plan aktif (setiap plan punya Cut Loss sendiri):",
    "Log:": "Log:",
    "Plan": "Plan",
    "Sent": "Terkirim",
    "BUY (filled / pending, CL)": "BUY (terisi / pending, CL)",
    "SELL (filled / pending, CL)": "SELL (terisi / pending, CL)",
    "Cut loss candle": "Candle cut loss",
    "No live plans.": "Tidak ada plan aktif.",
    "legacy timer": "timer lama",
    "Cancel entire plan and close all positions ({plan})":
        "Batalkan seluruh plan dan tutup semua posisi ({plan})",
    "Cancel PO buy and close buy position ({plan})":
        "Batalkan PO buy dan tutup posisi buy ({plan})",
    "Cancel PO sell and close sell position ({plan})":
        "Batalkan PO sell dan tutup posisi sell ({plan})",
    "Cancel PO {word} and close {word2} position ({plan})":
        "Batalkan PO {word} dan tutup posisi {word2} ({plan})",
    "Cut loss: OFF": "Cut loss: MATI",
    "Auto cut loss: OFF (only broker SL/TP protect positions)":
        "Auto cut loss: MATI (hanya SL/TP broker yang melindungi posisi)",
    "Cut-loss monitor: starting...": "Monitor cut loss: memulai...",
    "Cut-loss monitor: OK, last check {t}": "Monitor cut loss: OK, pengecekan terakhir {t}",
    "Cut-loss monitor: PROBLEM - {e}": "Monitor cut loss: MASALAH - {e}",
    "Current cut loss: legacy timer, {s}s ({name})":
        "Cut loss saat ini: timer lama, {s}dtk ({name})",
    "Current timeframe used: {tf} ({name})": "Timeframe yang dipakai: {tf} ({name})",
    "Next plan uses: legacy timer cut loss": "Plan berikutnya memakai: cut loss timer lama",
    "Next plan uses: {tf} candle close": "Plan berikutnya memakai: penutupan candle {tf}",
    # ---- manual input ----
    "{side} range:": "Range {side}:",
    "{side} cut loss:": "Cut loss {side}:",
    "{side} stop loss:": "Stop loss {side}:",
    "{side} take profit:": "Take profit {side}:",
    "add buy PO range": "tambah range PO buy",
    "add sell PO range": "tambah range PO sell",
    "add both buy and sell PO range": "tambah range PO buy dan sell",
    "Clear fields": "Hapus isian",
    "Layers and lots come from the MultiLayer / Layer amount boxes below. Leave a price empty to use the default shown next to it. A side with an empty range is left out.":
        "Layer dan lot diambil dari kotak MultiLayer / Jumlah layer di bawah. Kosongkan harga untuk memakai nilai bawaan di sebelahnya. Sisi dengan range kosong tidak dipakai.",
    "empty = 30% of the range {dir} it": "kosong = 30% dari range, {dir}nya",
    "empty = 30% of the range {dir} it: {price}": "kosong = 30% dari range, {dir}nya: {price}",
    "empty = 1:1 of the range, {dir} it": "kosong = 1:1 dari range, {dir}nya",
    "empty = 1:1 of the range, {dir} it: {price}": "kosong = 1:1 dari range, {dir}nya: {price}",
    "empty = {n} x the range {dir} it": "kosong = {n} x range, {dir}nya",
    "empty = {n} x the range {dir} it: {price}": "kosong = {n} x range, {dir}nya: {price}",
    "below": "di bawah",
    "above": "di atas",
    # ---- log lines ----
    "STARTED: {x}": "DIMULAI: {x}",
    "WARN: auto cut loss is OFF. Positions are protected only by their broker SL/TP.":
        "PERINGATAN: auto cut loss MATI. Posisi hanya dilindungi SL/TP broker.",
    "NOT SENT: {x}": "TIDAK TERKIRIM: {x}",
    "Cancelled.": "Dibatalkan.",
    "Confirmed by user.": "Dikonfirmasi pengguna.",
    "HALTED: {exc}\nPress Reconcile, then Send the same message again to resume.":
        "BERHENTI: {exc}\nTekan Rekonsiliasi, lalu Kirim pesan yang sama lagi untuk melanjutkan.",
    "ERROR (check the log and MT5 before retrying): {exc}":
        "ERROR (periksa log dan MT5 sebelum mencoba lagi): {exc}",
    "MT5 read failed: {x}": "Gagal membaca MT5: {x}",
    "Nothing to close in the {what}.": "Tidak ada yang perlu ditutup di {what}.",
    "entire plan {name}": "seluruh plan {name}",
    "{side} side of plan {name}": "sisi {side} dari plan {name}",
    "CLOSE FAILED: {exc}\nCheck MT5 by hand NOW.": "GAGAL MENUTUP: {exc}\nPeriksa MT5 secara manual SEKARANG.",
    "CLOSE ERROR: {exc}\nCheck MT5 by hand NOW.": "ERROR MENUTUP: {exc}\nPeriksa MT5 secara manual SEKARANG.",
    "Closed {what}: {c1} cancelled, {c2} closed.": "Ditutup {what}: {c1} dibatalkan, {c2} ditutup.",
    "KILL FAILED: {exc}\nCheck MT5 by hand NOW.": "GAGAL HENTIKAN SEMUA: {exc}\nPeriksa MT5 secara manual SEKARANG.",
    "KILL ERROR: {exc}\nCheck MT5 by hand NOW.": "ERROR HENTIKAN SEMUA: {exc}\nPeriksa MT5 secara manual SEKARANG.",
    "KILL: {c1} cancelled, {c2} closed.": "HENTIKAN SEMUA: {c1} dibatalkan, {c2} ditutup.",
    "No pending {side} orders to cancel.": "Tidak ada order {side} pending untuk dibatalkan.",
    "CANCEL FAILED: {exc}\nCheck MT5 by hand.": "GAGAL MEMBATALKAN: {exc}\nPeriksa MT5 secara manual.",
    "CANCEL ERROR: {exc}\nCheck MT5 by hand.": "ERROR MEMBATALKAN: {exc}\nPeriksa MT5 secara manual.",
    "Cancel {side}: {c1} pending cancelled, {c2} open positions left.":
        "Batalkan {side}: {c1} pending dibatalkan, {c2} posisi terbuka tersisa.",
    "No price from MT5; cannot tell which are in profit.":
        "Tidak ada harga dari MT5; tidak bisa menentukan mana yang profit.",
    "No {what}open positions to close.": "Tidak ada posisi {what}terbuka untuk ditutup.",
    "profitable": "yang profit",
    "profitable positions": "posisi yang profit",
    "all positions": "semua posisi",
    "Close {label}: {c1} closed, {c2} left open, {c3} pending orders left in place.":
        "Tutup {label}: {c1} ditutup, {c2} tetap terbuka, {c3} order pending dibiarkan.",
    "STOPPED: window closed.": "BERHENTI: jendela ditutup.",
    "RESTARTING to apply settings.": "MEMULAI ULANG untuk menerapkan pengaturan.",
    "Reconnect failed: {exc}": "Gagal menyambung ulang: {exc}",
    "Reconciled. {n} active orders.": "Rekonsiliasi selesai. {n} order aktif.",
    "STARTUP: {exc}": "STARTUP: {exc}",
    "Startup: {n} active ACTP orders.": "Startup: {n} order ACTP aktif.",
    "WARN: could not save the Layer amount: {exc}": "PERINGATAN: tidak bisa menyimpan jumlah layer: {exc}",
    "WARN: chart lines not written: {x}": "PERINGATAN: garis chart tidak ditulis: {x}",
    "WARN: chart lines failed: {x}": "PERINGATAN: garis chart gagal: {x}",
    "ALERT: Auto-TP halted the manager: {exc}": "PERINGATAN: Auto-TP menghentikan manajer: {exc}",
    "AUTO-TP: all {side} layers filled. TP moved to {t} on {n} positions.":
        "AUTO-TP: semua layer {side} terisi. TP dipindah ke {t} pada {n} posisi.",
    "AUTO-TP: all {side} layers filled, but price is already past {t}. TP not moved yet; retrying.":
        "AUTO-TP: semua layer {side} terisi, tetapi harga sudah melewati {t}. TP belum dipindah; mencoba lagi.",
    "AUTO-TP: MT5 refused the TP change on {side} ({n} moved). Retrying.":
        "AUTO-TP: MT5 menolak perubahan TP pada {side} ({n} dipindah). Mencoba lagi.",
    "MONITOR ERROR: {x}": "ERROR MONITOR: {x}",
    "{side} Cut Loss breached. Needs {n} to close.": "Cut Loss {side} tertembus. Butuh {n} untuk menutup.",
    "{side} candle closed beyond the Cut Loss: all {side2} orders were cancelled/closed.":
        "Candle {side} ditutup melewati Cut Loss: semua order {side2} dibatalkan/ditutup.",
    "{side} price recovered; Cut Loss timer reset.": "Harga {side} pulih; timer Cut Loss direset.",
    "Cut-loss monitoring recovered.": "Pemantauan cut loss pulih.",
    "Cut-loss check halted: {exc}": "Pengecekan cut loss berhenti: {exc}",
    # ---- popups ----
    "Confirm": "Konfirmasi",
    "{preview}\n\nSend this to your account?": "{preview}\n\nKirim ini ke akun Anda?",
    "Cancel": "Batal",
    "Close": "Tutup",
    "Restart": "Mulai ulang",
    "Settings saved. They apply after a restart.\n\nRestart ACTP now? Cut Loss monitoring pauses for a few seconds; open orders keep their broker SL/TP.":
        "Pengaturan disimpan. Berlaku setelah dimulai ulang.\n\nMulai ulang ACTP sekarang? Pemantauan Cut Loss berhenti beberapa detik; order terbuka tetap memakai SL/TP broker.",
    "Cancel every pending ACTP order and close every open ACTP position?\n\n(Orders not placed by ACTP are not touched.)":
        "Batalkan semua order pending ACTP dan tutup semua posisi terbuka ACTP?\n\n(Order yang bukan dari ACTP tidak disentuh.)",
    "Cancel orders": "Batalkan order",
    "Cancel {n} pending {side} order(s) (all plans)?\n\nOpen {side2} positions ({m}) are NOT touched.":
        "Batalkan {n} order {side} pending (semua plan)?\n\nPosisi {side2} yang terbuka ({m}) TIDAK disentuh.",
    "Close positions": "Tutup posisi",
    "Close {n} {what}open ACTP position(s)?\n\nPending orders are NOT touched.":
        "Tutup {n} posisi ACTP {what}terbuka?\n\nOrder pending TIDAK disentuh.",
    "Close plan": "Tutup plan",
    "Close {side}": "Tutup {side}",
    "Cancel {p} pending order(s) and close {o} open position(s) of the {what}?\n\nOther plans are not touched.":
        "Batalkan {p} order pending dan tutup {o} posisi terbuka dari {what}?\n\nPlan lain tidak disentuh.",
    "Quit": "Keluar",
    "Closing this window stops Cut Loss and Auto-TP monitoring. Orders keep their own SL/TP at the broker.\n\nQuit anyway?":
        "Menutup jendela ini menghentikan pemantauan Cut Loss dan Auto-TP. Order tetap memakai SL/TP di broker.\n\nTetap keluar?",
    "ACTP - read first": "ACTP - baca dulu",
    "ACTP places REAL orders on whichever MetaTrader 5 account is logged in, and can lose money.\n\n- Try it on a DEMO account first.\n- It only understands one signal format (PLAN XAUUSDr ...).\n- It has no warranty. You are responsible for every order.\n\nContinue?":
        "ACTP menempatkan order ASLI di akun MetaTrader 5 mana pun yang sedang login, dan bisa merugikan.\n\n- Coba dulu di akun DEMO.\n- Hanya memahami satu format sinyal (PLAN XAUUSDr ...).\n- Tanpa garansi. Anda bertanggung jawab atas setiap order.\n\nLanjutkan?",
    "LIVE - REAL MONEY": "LIVE - UANG ASLI",
    "LIVE - REAL MONEY - {server}": "LIVE - UANG ASLI - {server}",
    "DEMO ACCOUNT - {server}": "AKUN DEMO - {server}",
    "Cannot save: {exc}": "Tidak bisa menyimpan: {exc}",
    "Layer {key} appears twice.": "Layer {key} muncul dua kali.",
    "No XAUUSD symbol found. Open MT5, log in, and try again, or type the symbol name yourself.":
        "Simbol XAUUSD tidak ditemukan. Buka MT5, login, lalu coba lagi, atau ketik nama simbol sendiri.",
    "Cannot read your settings file:\n{err}\n\nFix or delete it, then start ACTP again.":
        "Tidak bisa membaca file pengaturan:\n{err}\n\nPerbaiki atau hapus, lalu jalankan ACTP lagi.",
    "Cannot start: {exc}\n\nOpen MetaTrader 5, log in, turn on Algo Trading, then start ACTP again.":
        "Tidak bisa memulai: {exc}\n\nBuka MetaTrader 5, login, aktifkan Algo Trading, lalu jalankan ACTP lagi.",
    "Cannot create the log file: {exc}": "Tidak bisa membuat file log: {exc}",
    "This account is not a HEDGING account.\n\nACTP places several orders per side. On a netting account they merge into one position, which breaks Cut Loss and the close buttons.\n\nOpen (or ask your broker for) a hedging account, then start ACTP again.":
        "Akun ini bukan akun HEDGING.\n\nACTP menempatkan beberapa order per sisi. Pada akun netting, order tersebut menyatu menjadi satu posisi, sehingga Cut Loss dan tombol tutup tidak berfungsi.\n\nBuka (atau minta ke broker) akun hedging, lalu jalankan ACTP lagi.",
    # ---- code popup ----
    "Type the 5 digits below to confirm:": "Ketik 5 angka di bawah untuk mengonfirmasi:",
    "Turn off confirmation": "Matikan konfirmasi",
    "Are you sure you want to untick {what}?\n\nWithout it, the action runs with no popup at all.":
        "Yakin ingin menghapus centang {what}?\n\nTanpa itu, aksi berjalan tanpa popup sama sekali.",
    "the popup confirmation of ALL actions": "konfirmasi popup SEMUA aksi",
    "the popup confirmation of '{name}'": "konfirmasi popup '{name}'",
    # ---- settings ----
    "ACTP settings": "Pengaturan ACTP",
    "XAUUSD symbol on your broker": "Simbol XAUUSD di broker Anda",
    "Detect from MT5": "Deteksi dari MT5",
    "Lot per order": "Lot per order",
    "Saved MultiLayer...": "MultiLayer tersimpan...",
    "Manual SL = ": "SL Manual = ",
    " x range": " x range",
    "Max layers bought/sold at market per side": "Maks. layer dibeli/dijual di harga pasar per sisi",
    "Auto cut loss (close a plan when its Cut Loss breaks)":
        "Auto cut loss (tutup plan saat Cut Loss tertembus)",
    "Candle timeframe: a candle must CLOSE beyond the Cut Loss":
        "Timeframe candle: candle harus DITUTUP melewati Cut Loss",
    "Use the legacy timer instead (hold time + SCL, no candles)":
        "Pakai timer lama (waktu tahan + SCL, tanpa candle)",
    "Hold time before closing (seconds)": "Waktu tahan sebelum menutup (detik)",
    "Stop Cut Loss (SCL): price must also reach it": "Stop Cut Loss (SCL): harga juga harus mencapainya",
    "SCL distance (% of the way, Cut Loss to SL)": "Jarak SCL (% dari jalur Cut Loss ke SL)",
    "Popup configurations (Dangerous settings)": "Konfigurasi popup (Pengaturan berbahaya)",
    "Popup configurations": "Konfigurasi popup",
    "Popup confirmation (OK / Cancel before each action)":
        "Konfirmasi popup (OK / Batal sebelum setiap aksi)",
    "Configure each popup...": "Atur tiap popup...",
    "Unticking a box asks you to type a random 5-digit code. An action with its box unticked runs with no popup at all.":
        "Menghapus centang meminta Anda mengetik kode 5 angka acak. Aksi yang kotaknya tidak dicentang berjalan tanpa popup sama sekali.",
    "Changes apply after ACTP restarts, and only to plans sent afterwards: each plan keeps the cut loss rule and timeframe it was sent with. With auto cut loss OFF, nothing closes a plan automatically: only the broker's SL/TP, KILL ALL, or you.":
        "Perubahan berlaku setelah ACTP dimulai ulang, dan hanya untuk plan yang dikirim sesudahnya: setiap plan mempertahankan aturan cut loss dan timeframe saat dikirim. Dengan auto cut loss MATI, tidak ada yang menutup plan otomatis: hanya SL/TP broker, HENTIKAN SEMUA, atau Anda sendiri.",
    "Lot:": "Lot:",
    "Lot not changed: {x}": "Lot tidak diubah: {x}",
    "Lot per order is now {x}.": "Lot per order sekarang {x}.",
    "NOT SENT: fix the Lot box first.": "TIDAK TERKIRIM: perbaiki kotak Lot dulu.",
    "Candle:": "Candle:",
    "(MultiLayer wins if filled)": "(MultiLayer diutamakan jika diisi)",
    "Candle timeframe for NEW plans": "Timeframe candle untuk plan BARU",
    "Candle timeframe not changed: {x}": "Timeframe candle tidak diubah: {x}",
    "Candle timeframe for NEW plans is now {tf}. Plans already sent keep theirs.":
        "Timeframe candle untuk plan BARU sekarang {tf}. Plan yang sudah terkirim tetap memakai yang lama.",
    "Create a desktop shortcut": "Buat pintasan di desktop",
    "The desktop shortcut was not created:\n{x}": "Pintasan desktop tidak dibuat:\n{x}",
    "(next plan)": "(plan berikutnya)",
    "LIVE - SERVER - {server}": "LIVE - SERVER - {server}",
    "LIVE - No server found": "LIVE - Server tidak ditemukan",
    "Language": "Bahasa",
    "Change theme": "Ganti tema",
    "Theme changed. It applies after a restart.\n\nRestart ACTP now? Cut Loss monitoring pauses for a few seconds; open orders keep their broker SL/TP.":
        "Tema diubah. Berlaku setelah dimulai ulang.\n\nMulai ulang ACTP sekarang? Pemantauan Cut Loss berhenti beberapa detik; order terbuka tetap memakai SL/TP broker.",
    "Current timeframe used:": "Timeframe yang dipakai:",
    "Current cut loss:": "Cut loss saat ini:",
    "Next plan uses:": "Plan berikutnya memakai:",
    "Cut loss:": "Cut loss:",
    "OFF": "MATI",
    "Plan applied: {n} pending orders.": "Plan diterapkan: {n} order pending.",
    "Plan applied: {n} pending orders, {m} placed at market.":
        "Plan diterapkan: {n} order pending, {m} dipasang di harga pasar.",
    "Update {side}: {a} modified, {b} unchanged.": "Ubah {side}: {a} diubah, {b} tidak berubah.",
    "Update {side}: {a} modified, {b} unchanged, {c} value(s) not valid for some orders, old value kept.":
        "Ubah {side}: {a} diubah, {b} tidak berubah, {c} nilai tidak valid untuk sebagian order, nilai lama dipertahankan.",
    "Close {side}: {a} closed, {b} pending {side2} orders left in place.":
        "Tutup {side}: {a} ditutup, {b} order {side2} pending dibiarkan.",
    "{n} layers per side ({source}); layer 1 first: {lots}  (total {t} per side)":
        "{n} layer per sisi ({source}); layer 1 lebih dulu: {lots}  (total {t} per sisi)",
    "typed MultiLayer": "MultiLayer yang diketik",
    "saved MultiLayer": "MultiLayer tersimpan",
    "default 1x": "bawaan 1x",
    "Save": "Simpan",
    "Saved MultiLayer": "MultiLayer tersimpan",
    "Used when the MultiLayer box on the main window is empty and the Layer amount is that number. A layer amount with no entry here (or an empty one) uses 1x for every layer.":
        "Dipakai saat kotak MultiLayer di jendela utama kosong dan Jumlah layer sama dengan angka itu. Jumlah layer tanpa isian di sini (atau kosong) memakai 1x untuk setiap layer.",
    "Layer": "Layer",
    "MultiLayer": "MultiLayer",
    "Add new layer": "Tambah layer baru",
    "Delete this layer": "Hapus layer ini",
    # ---- popup action names (confirm.ACTIONS) ----
    "Send plan": "Kirim plan",
    "Cancel PO buy / sell": "Batalkan PO buy / sell",
    "Plan table: cancel PO + close one side": "Tabel plan: batalkan PO + tutup satu sisi",
    "Plan table: cancel entire plan": "Tabel plan: batalkan seluruh plan",
    "Quit ACTP": "Keluar dari ACTP",
}

_LEVELS = (("ALERT: ", "PERINGATAN: "), ("WARN: ", "PERINGATAN: "),
           ("INFO: ", "INFO: "))
_exact: dict[str, str] = {}
_patterns: list[tuple[re.Pattern[str], str, list[str]]] = []
_language = DEFAULT_LANGUAGE


def _compile() -> None:
    _exact.clear()
    _patterns.clear()
    ranked = []
    for english, indonesian in ID.items():
        names = re.findall(r"\{(\w+)\}", english)
        if not names:
            _exact[english] = indonesian
            continue
        parts = re.split(r"\{\w+\}", english)
        regex = "(.*?)".join(re.escape(p) for p in parts)
        ranked.append(
            (sum(len(p) for p in parts),
             re.compile(f"^{regex}$", re.DOTALL), indonesian, names))
    ranked.sort(key=lambda item: -item[0])
    for _score, regex, indonesian, names in ranked:
        _patterns.append((regex, indonesian, names))


_compile()


def set_language(code: str) -> None:
    global _language
    _language = code if code in LANGUAGES else DEFAULT_LANGUAGE


def language() -> str:
    return _language


def tr(text: Any, _depth: int = 0) -> Any:
    """The Indonesian text for an English one (English when not Indonesian
    or when there is no entry)."""
    if _language != "id" or not isinstance(text, str) or not text.strip():
        return text
    lead = text[: len(text) - len(text.lstrip())]
    tail = text[len(text.rstrip()):]
    core = text.strip()
    found = _exact.get(core)
    if found is not None:
        return lead + found + tail
    for prefix, word in _LEVELS:  # log level in front of an event text
        if core.startswith(prefix):
            return lead + word + tr(core[len(prefix):], _depth + 1) + tail
    if _depth < 3:
        for regex, indonesian, names in _patterns:
            match = regex.match(core)
            if match is None:
                continue
            values = {n: tr(v, _depth + 1) for n, v in zip(names, match.groups())}
            return lead + indonesian.format(**values) + tail
    return text
