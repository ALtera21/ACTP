🇬🇧 [English](README.gb.md)

# ACTP – Auto Copy Trade Plan

ACTP memasang serangkaian pending order berlapis di MT5 berdasarkan rencana trading yang Anda tempelkan, lalu mengelolanya setelah itu (Cut Loss, tombol penutupan, Auto-TP). ACTP tidak membaca WhatsApp secara otomatis: Anda harus menyalin dan menempelkan setiap pesan secara manual.

> [!WARNING]
> ACTP memasang **order sungguhan** pada akun MetaTrader 5 yang sedang login dan dapat menyebabkan kerugian. ACTP diberikan **TANPA JAMINAN APA PUN** (lihat file LICENSE). ACTP bukan nasihat keuangan dan tidak berafiliasi dengan penyedia sinyal, WhatsApp, MetaQuotes, atau broker mana pun. Anda bertanggung jawab atas setiap order. Uji terlebih dahulu menggunakan akun demo.

## 💻 Persyaratan

Sebelum memasang ACTP, pastikan Anda memiliki:

- Windows 10 atau Windows 11 (64-bit).
- Terminal desktop MetaTrader 5.
- Akun MT5 bertipe hedging. Pada akun netting, layer dari satu sisi akan digabung menjadi satu posisi. ACTP tidak akan berjalan pada akun netting.
- Algo Trading dalam keadaan AKTIF pada toolbar MT5.
- MT5 harus tetap terbuka dan sudah login selama ACTP digunakan.
- Broker Anda menyediakan simbol XAUUSD (XAUUSD, XAUUSDc, XAUUSDm, XAUUSD.r, dan sebagainya).

## 📥 Unduh

Unduh rilis ACTP terbaru dari halaman GitHub Releases resmi:

**[KLIK DI SINI UNTUK MENGUNDUH ACTP – RILIS TERBARU](https://github.com/ALtera21/ACTP/releases/download/v1.0.1/dist.zip)**

1. Unduh file `.zip` yang tersedia di bagian **Assets**.
2. Ekstrak ZIP ke direktori yang Anda inginkan.
3. Jalankan `ACTP.exe` dari folder hasil ekstraksi. Biarkan file tersebut tetap berada bersama folder `_internal`; jangan pindahkan file exe ke lokasi lain atau menjalankannya langsung dari dalam file ZIP.

> **Catatan:** Windows SmartScreen mungkin menampilkan peringatan karena aplikasi belum ditandatangani: pilih “More info → Run anyway”. File exe PyInstaller juga dapat memicu deteksi antivirus yang keliru (false positive).

Contoh:

```text
C:\ACTP
```

**Verifikasi file unduhan (v1.0.1):** jalankan perintah berikut di PowerShell:

```powershell
Get-FileHash .\dist.zip -Algorithm SHA256
```

Lalu bandingkan hasilnya dengan: `4bde5ce45219f045849a0242b31b9d43617ad859e1848a86d278ea16441364ac`

## 🚀 Pengaturan ACTP

1. Buka MetaTrader 5, login, lalu aktifkan **Algo Trading**.
2. Klik dua kali `ACTP.exe`.
3. **Hanya saat pertama kali dijalankan:** baca pemberitahuan risiko lalu klik OK. Jendela Settings akan terbuka:
   - Pilih simbol XAUUSD milik broker Anda (**Detect from MT5** akan mengisi daftar secara otomatis).
   - Atur lot untuk setiap order.
   - Pilih opsi Cut Loss (lihat penjelasan di bawah).
   - Klik **Save**. ACTP akan memulai ulang secara otomatis.
4. Banner pada jendela menunjukkan jenis akun yang sedang digunakan: **merah = UANG SUNGGUHAN**, **hijau = DEMO**. Cobalah terlebih dahulu menggunakan akun demo.
5. Salin rencana trading dari WhatsApp, tempelkan ke kotak teks, klik **Send**, periksa ringkasannya, lalu konfirmasikan.

Pengaturan, log, dan rencana yang tersimpan berada di `%APPDATA%\ACTP` (`settings.json`, `logs\`, `state\`). Untuk menghapus ACTP, hapus folder ACTP dan `%APPDATA%\ACTP`.

**Penting:** pengaturan hanya dapat disimpan ketika MT5 sedang terbuka karena ACTP perlu memeriksa simbol dan lot berdasarkan batasan broker Anda.

## 🖥️ Menggunakan ACTP

### Pesan yang dapat diproses ACTP

| Pesan | Tindakan ACTP |
|---|---|
| `PLAN XAUUSDr` dengan bagian BUY LIMIT dan/atau SELL LIMIT | Mengganti rencana yang sedang aktif pada sisi tersebut dan memasang satu pending order untuk setiap layer. |
| `UPDATE XAUUSDr BUY\|SELL` dengan `SL: a ➜ b` dan/atau `TP: a ➜ b` | Mengubah SL/TP pada order di sisi tersebut. |
| `CLOSE XAUUSDr BUY\|SELL` | Menutup **posisi yang sudah terbuka** pada sisi tersebut. Pending order di sisi tersebut tetap aktif. |
| `XAUUSDr BUY\|SELL - Entry ter-fill` | Hanya dicatat. Tidak ada order yang dikirim. |

Batasan: 2 hingga 20 layer per sisi, maksimal 12 order untuk setiap rencana, dan harga harus menggunakan 2 angka desimal. Teks yang sama persis dan ditempelkan dua kali dalam rentang 30 detik akan ditolak.

### Kontrol jendela

| Kontrol | Fungsi |
|---|---|
| **Paste box** | Tempat Anda menempelkan pesan. |
| **Send** | Memproses pesan, menampilkan ringkasan (termasuk jumlah layer yang akan terisi pada harga pasar saat ini), lalu mengirimkannya setelah Anda mengonfirmasi. |
| **Clear** | Mengosongkan kotak teks. |
| **Settings...** | Membuka jendela pengaturan. Perubahan akan diterapkan setelah ACTP dimulai ulang (ACTP akan menawarkan untuk melakukannya). |
| **Reconcile (clear halt)** | Setelah operasi gagal, ACTP akan berhenti (status menunjukkan HALTED). Periksa MT5, tekan tombol ini untuk membaca ulang kondisi akun, lalu kirim ulang pesan yang sama untuk melanjutkan. (Anggap saja tombol ini sebagai tombol refresh.) |
| **KILL ALL** | Membatalkan semua pending order ACTP dan menutup semua posisi ACTP yang terbuka. Tindakan ini memerlukan konfirmasi. |
| **Close all positions** | Hanya menutup posisi yang terbuka. Pending order tetap aktif. |
| **Close profitable only** | Hanya menutup posisi yang sedang menghasilkan profit. Spread diperhitungkan; swap dan komisi tidak diperhitungkan. |
| Kotak centang **Auto-TP** | Ketika semua layer pada suatu sisi sudah terisi, TP sisi tersebut dipindahkan ke harga entry pertama atau `proximal` (BUY: layer tertinggi, SELL: layer terendah). Dilakukan satu kali untuk setiap rencana dan sisi. Fitur ini selalu NONAKTIF setiap kali ACTP dijalankan. |

> [!NOTE]
> ACTP hanya mengelola order yang dipasangnya sendiri. Transaksi manual Anda tidak akan diubah.

<p align="center">
  <img src="docs\main-screen.png" width="400" alt="Dasbor utama">
</p>

<p align="center">
  <img src="docs\halted.png" width="400" alt="Terjadi kesalahan dan sistem berhenti; tekan tombol Reconcile">
</p>

### Rencana yang terlambat

Jika Anda menempelkan rencana setelah harga bergerak masuk ke area rencana tersebut, layer yang harga entry-nya sudah terlewati akan dibeli **pada harga pasar (market)**, bukan ditolak. Ringkasan pada tombol Send menunjukkan pembagian layer tersebut. Rencana akan ditolak jika:

- Jumlah layer yang akan terisi pada harga pasar melebihi **Max layers at market per side**, atau
- Harga sudah melewati Cut Loss atau SL pada sisi tersebut.

### Cut Loss

Jika **Auto cut loss** aktif, ACTP akan menutup seluruh order pada satu sisi (membatalkan pending order dan menutup posisi yang terbuka) ketika:

1. Harga (BUY: bid, SELL: ask) tetap berada di luar batas Cut Loss pada rencana selama **hold time** tanpa terputus, **dan**
2. Jika **SCL** aktif, harga juga harus sudah mencapai atau melewati garis Stop Cut Loss pada saat itu.

SCL berada di antara Cut Loss dan SL, pada persentase jarak yang dipilih dari Cut Loss menuju SL. Jika jeda antara pemeriksaan harga lebih dari 15 detik, timer akan dimulai ulang. Jika Auto cut loss **nonaktif**, tidak ada sisi yang akan ditutup secara otomatis: penutupan hanya dilakukan oleh SL/TP broker, KILL ALL, atau oleh Anda sendiri.

**Cut Loss dan Auto-TP hanya berjalan selama jendela ACTP tetap terbuka.** Saat Anda menutup jendela, ACTP akan meminta konfirmasi karena pemantauan akan berhenti. Order tetap memiliki SL/TP yang tersimpan di broker.

## 📋 Konfigurasi Trading

Atur opsi berikut melalui **Settings...**. Nilai bawaan ditampilkan di bawah ini.

| Pengaturan | Bawaan | Nilai yang diizinkan | Keterangan |
|---|---|---|---|
| Simbol XAUUSD | `XAUUSDc` | Varian XAUUSD apa pun (`XAUUSD`, `XAUUSDm`, `XAUUSD.r`, ...) | Satu-satunya simbol yang akan ditransaksikan ACTP. Pesan selalu menggunakan `XAUUSDr`; itu adalah nama simbol dari penyedia sinyal, bukan nama simbol broker Anda. |
| Lot per order | 0.01 | 0.01 hingga 10, kelipatan 0.01 | Volume untuk setiap layer. Harus sesuai dengan batasan broker Anda. |
| Max layers at market per side | 3 | 0 hingga 12 | Lihat bagian "Rencana yang terlambat". |
| Auto cut loss | Aktif | Aktif / Nonaktif | Lihat bagian "Cut Loss". |
| Hold time | 60 dtk | 10 hingga 3600 | Durasi pelanggaran batas Cut Loss yang harus berlangsung sebelum tindakan dilakukan. |
| SCL | Aktif | Aktif / Nonaktif | Harga juga harus mencapai garis Stop Cut Loss. |
| SCL distance | 15% | 5 hingga 95 | Jarak garis SCL dari Cut Loss menuju SL, dalam persentase. |

Simbol Anda harus mendukung setidaknya 2 angka desimal pada harga.

<p align="center">
  <img src="docs\settings.png" width="400" alt="Jendela pengaturan">
</p>

> **Catatan:**
> Ukuran lot yang tercantum dalam rencana trading WhatsApp asli akan diabaikan. ACTP menggunakan volume yang Anda atur sendiri.

## ⚠️ Catatan Penting

- ACTP tidak memiliki mode dry-run. Uji terlebih dahulu menggunakan akun demo, jalankan satu salinan ACTP untuk setiap akun, pastikan PC tidak masuk mode tidur dan jendela ACTP tetap terbuka, periksa banner serta server, dan pahami bahwa semua risiko menjadi tanggung jawab Anda.
- Pastikan terminal MT5 terhubung ke akun trading yang benar.
- Jangan pernah menggunakan rilis yang belum diverifikasi untuk trading langsung.
- Trading memiliki risiko finansial. Anda bertanggung jawab untuk mengatur dan memantau lingkungan trading Anda.

## 🐛 Pemecahan Masalah

| Pesan | Tindakan |
|---|---|
| `Cannot start: ...` | Buka MetaTrader 5, login, aktifkan Algo Trading, lalu jalankan ACTP kembali. |
| `Cannot read your settings file` | Perbaiki atau hapus `%APPDATA%\ACTP\settings.json`. |
| `Status: HALTED` | Periksa MT5, tekan **Reconcile**, lalu kirim ulang pesan yang sama untuk melanjutkan. |
| Windows memblokir aplikasi | Pilih More info → Run anyway (lihat bagian Unduh). |
| This account is not a HEDGING account | Gunakan akun bertipe hedging. Hubungi broker Anda jika diperlukan. |

**Log:** ACTP membuat satu file log per hari di `%APPDATA%\ACTP\logs\actp.log` (menyimpan log selama 30 hari). Tempelkan `%APPDATA%\ACTP\logs` pada bilah alamat File Explorer untuk membuka folder tersebut. Jika Anda melaporkan masalah, sertakan baris log yang relevan. Log berisi nama server broker, tiket order, dan harga; periksa informasi tersebut sebelum membagikannya secara publik.

Laporkan bug dan ajukan pertanyaan melalui issue pada repositori GitHub.

## 📄 Lisensi

Lihat file LICENSE pada repositori untuk mengetahui ketentuan lisensi dan penggunaan.