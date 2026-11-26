🇬🇧 [English](README.gb.md)

# ACTP – Auto Copy Trade Plan

<p align="center">
  <a href="https://youtu.be/d0bM5Vmml2w">
    <img src="docs\mylogo.png" width="256" alt="icon">
  </a>
</p>

ACTP memasang sekumpulan order pending MT5 berlapis dari trade plan yang Anda tempel (atau isi manual di tab **Input Manual**), lalu mengelola setiap plan sesudahnya (Cut Loss, update TP, re-entry, tombol tutup, Auto-TP, Auto remove). ACTP tidak membaca WhatsApp untuk Anda: Anda menyalin dan menempel setiap pesan sendiri.

> [!WARNING]
> ACTP memasang **order sungguhan** di akun MetaTrader 5 yang sedang login dan bisa membuat Anda rugi. Tidak ada **JAMINAN APA PUN** (lihat file LICENSE). Ini bukan saran keuangan dan tidak berafiliasi dengan penyedia sinyal, WhatsApp, MetaQuotes, maupun broker mana pun. Anda bertanggung jawab atas setiap order. Cobalah dulu di akun demo.

## 🎬 Video tutorial

<p align="center">
  <a href="https://youtu.be/d0bM5Vmml2w">
    <img src="docs\ACTP tutorial.png" width="500" alt="Video tutorial ACTP">
  </a>
</p>

> Video ini menampilkan v1.0.2. Lihat [catatan rilis](https://github.com/ALtera21/ACTP/releases) untuk yang baru di v2.1.0.

## 💻 Persyaratan

Sebelum memasang ACTP, pastikan Anda punya:

- Windows 10 atau Windows 11 (64-bit).
- Terminal desktop MetaTrader 5.
- Akun MT5 **hedging**. Di akun netting, layer satu sisi menyatu menjadi satu posisi. ACTP menolak berjalan di akun netting.
- Algo Trading dalam keadaan HIDUP di toolbar MT5.
- MT5 harus terbuka dan sudah login selama ACTP berjalan.
- Broker Anda menyediakan simbol XAUUSD (XAUUSD, XAUUSDc, XAUUSDm, XAUUSD.r dan seterusnya).

## 📥 Unduh

Unduh rilis ACTP terbaru dari halaman GitHub Releases resmi:

**[KLIK DI SINI UNTUK MENGUNDUH ACTP – RILIS TERBARU](https://github.com/ALtera21/ACTP/releases/download/v2.1.1/dist.zip)**

1. Unduh file `.zip` yang tersedia di bagian **Assets**.
2. Ekstrak file ZIP ke folder pilihan Anda.
3. Jalankan `ACTP.exe` dari folder hasil ekstrak. Biarkan tetap bersama folder `_internal`; jangan pindahkan exe ke tempat lain atau menjalankannya dari dalam ZIP.

> **Catatan:** Windows SmartScreen bisa memperingatkan soal aplikasi tanpa tanda tangan: “More info → Run anyway”. Exe buatan PyInstaller juga sering memicu false positive antivirus.

Contoh:

```text
C:\ACTP
```

**Verifikasi unduhan Anda (v2.1.0):** di PowerShell jalankan
```powershell
Get-FileHash .\dist.zip -Algorithm SHA256
```
lalu bandingkan dengan: `b845b7dcb5b483abd13a72c0a14a9e976c885d8f99337f828ee497d7b18848af`

**Update dari v2.0.x:** tutup ACTP, ekstrak v2.1.0 ke folder baru dan jalankan. Pengaturan dan data tersimpan di `%APPDATA%\ACTP` ikut terbawa, dan plan yang masih terbuka tetap berjalan. Pilihan yang sudah Anda simpan (misalnya Auto-TP mati) tetap dipakai; nilai bawaan yang baru hanya berlaku untuk pengaturan yang belum pernah disimpan.

**Update dari v1.1.0:** langkahnya sama. Plan yang masih terbuka dari v1.1.0 tetap memakai Cut Loss timer-tahan yang lama.

## 🚀 Persiapan ACTP

1. Buka MetaTrader 5, login, dan hidupkan **Algo Trading**.
2. Klik dua kali `ACTP.exe`.
3. **Hanya saat pertama kali:** baca pemberitahuan risiko lalu klik OK. Jendela Pengaturan terbuka:
   - Pilih simbol XAUUSD broker Anda (**Deteksi dari MT5** mengisi daftarnya).
   - Pilih opsi Cut Loss Anda (lihat di bawah).
   - Centang **Buat pintasan di desktop** jika Anda mau.
   - Klik **Simpan**. ACTP restart sendiri.
4. Banner jendela menunjukkan akun yang sedang dipakai: **biru tua = LIVE (uang sungguhan)**, **hijau = DEMO**, beserta nama server. Cobalah dulu di akun demo.
5. Atur **Lot** dan timeframe **Candle** di jendela utama, salin trade plan dari WhatsApp, tempel ke kotaknya, klik **Kirim**, baca ringkasannya, lalu konfirmasi.

Pengaturan, log, dan plan tersimpan Anda ada di `%APPDATA%\ACTP` (`settings.json`, `logs\`, `state\`). Untuk mencopot, hapus folder ACTP dan `%APPDATA%\ACTP`.

**Penting:** pengaturan hanya bisa disimpan saat MT5 terbuka, karena ACTP memeriksa simbol dan lot terhadap broker Anda.

## 🖥️ Memakai ACTP

### Pesan yang dikenali ACTP

| Pesan | Yang dilakukan ACTP |
|---|---|
| `PLAN XAUUSDr` dengan bagian BUY LIMIT dan/atau SELL LIMIT | Memulai **plan baru** dan memasang satu order pending per layer. Plan lama **tidak** digantikan; setiap plan berdiri sendiri. |
| `UPDATE XAUUSDr BUY\|SELL` dengan `SL: a ➜ b` dan/atau `TP: a ➜ b` | Mengubah SL/TP sisi itu pada plan **terbaru** yang memilikinya. |
| `CLOSE XAUUSDr BUY\|SELL` | Menutup **posisi terbuka** sisi itu di semua plan. Order pending sisi itu tetap. |
| `XAUUSDr BUY\|SELL - Entry ter-fill` | Hanya dicatat. Tidak ada yang dikirim. |

Batas: 2 sampai 200 layer per sisi, maksimal 400 order per plan, harga dengan 2 desimal. Menempel teks yang persis sama dua kali dalam 30 detik ditolak.

### Kontrol jendela

| Kontrol | Fungsinya |
|---|---|
| Tab **Tempel pesan** | Tempat menempel pesan. |
| Tab **Input Manual** | Membuat plan secara manual (lihat "Input Manual" di bawah). |
| Kotak **MultiLayer** | Opsional. Mengatur pengali lot tiap layer dan jumlah layer untuk plan yang Anda kirim berikutnya (lihat "MultiLayer" di bawah). |
| **Jumlah layer** | Berapa layer yang didapat plan berikutnya saat MultiLayer kosong (2 sampai 200). |
| **Lot** | Lot dasar tiap order. Perubahan langsung berlaku dan disimpan. Dicek terhadap minimum, kelipatan, dan maksimum broker Anda. |
| **Candle** | Timeframe candle untuk Cut Loss pada plan yang Anda kirim mulai sekarang (M1 sampai H1). Plan yang sudah terkirim tetap memakai miliknya. Abu-abu saat auto cut loss mati atau timer lama dipakai. |
| ☾ / ☀ | Berganti antara tema terang dan gelap (berlaku setelah restart). |
| **Kirim** | Membaca pesan, menampilkan ringkasan (termasuk berapa layer yang akan terisi di harga pasar saat ini), lalu mengirimnya setelah Anda konfirmasi. |
| **Hapus** | Mengosongkan kotak tempel. |
| **Pengaturan...** | Membuka jendela pengaturan. Perubahan berlaku setelah restart (ACTP menawarkannya). |
| **Rekonsiliasi (hapus henti)** | Setelah operasi gagal, ACTP berhenti (status menunjukkan HALTED / BERHENTI). Periksa MT5, tekan ini untuk membacanya ulang, lalu Kirim pesan yang sama lagi untuk melanjutkan. (Anggap ini tombol refresh) |
| **HENTIKAN SEMUA** (KILL ALL) | Membatalkan setiap order pending ACTP dan menutup setiap posisi terbuka ACTP, di semua plan. |
| **Tutup semua posisi** | Hanya menutup posisi terbuka. Order pending tetap. |
| **Tutup yang profit saja** | Hanya menutup posisi yang sedang profit. Spread dihitung; swap dan komisi tidak. |
| **Batalkan order buy** / **Batalkan order sell** | Membatalkan order **pending** sisi itu di semua plan. Posisi terbuka tetap terbuka dan tetap memiliki SL/TP serta perlindungan Cut Loss. Tidak berjalan saat HALTED: tekan Rekonsiliasi dulu. |
| Kotak centang **Update TP juga mengubah order pending** | Di sebelah judul Plan aktif. Mati: tombol ↻ Update TP hanya mengubah posisi terbuka. Hidup: order pending juga mendapat TP baru. Diingat. Hidup secara bawaan. |
| Kotak centang dan kotak **Auto-TP** | "Auto-TP: saat harga [100] % di dalam range satu sisi, pindahkan TP ke proximal". Lihat "Auto-TP" di bawah. Diingat. Hidup secara bawaan. |
| Kotak centang **Auto-TP diprioritaskan di atas update TP manual** | Membuat Auto-TP menggantikan TP yang Anda atur manual (arahkan kursor ke ⓘ). Diingat. Hidup secara bawaan. |
| Kotak centang dan kotak **Auto remove** | "Hapus sisi PLAN yang tidak punya posisi terbuka dan [90] % di dalam range satu sisi". Lihat "Auto remove" di bawah. Diingat. Mati secara bawaan. |

Sebagian besar meminta konfirmasi dulu; lihat "Konfirmasi popup" di bawah.

> [!NOTE]
> ACTP hanya menyentuh order yang dipasangnya sendiri. Trade manual Anda tidak pernah diubah.

<p align="center">
  <img src="docs\main-screen.png" width="400" alt="Dasbor proyek">
</p>

<p align="center">
  <img src="docs\halted.png" width="400" alt="Error halted, tekan tombol rekonsiliasi">
</p>

### Plan aktif (Live plans)

Setiap plan yang Anda kirim mendapat nama: **Alpha, Beta, Gamma … Omega** (setelah itu id acak pendek). Tabel **Plan aktif** menampilkan satu baris per plan berisi waktu kirim, order BUY dan SELL (terisi / pending, Cut Loss), dan hitung mundur candle-nya. Nama yang sama ada di komentar order MT5, misalnya `1763440000-Alpha:3` (waktu kirim, nama plan, nomor layer).

Setiap sisi pada satu baris punya **kotak TP**, tombol **↻ Update TP**, dan tombol **⇄ re-entry**, disusul tombol ✕ (arahkan kursor ke tombol mana pun untuk membaca fungsinya):

| Tombol | Fungsinya |
|---|---|
| **Kotak TP** + **↻** | Menampilkan TP yang berlaku. Ketik harga baru lalu tekan ↻ (atau Enter) untuk mengubah TP sisi plan itu saja. Lihat "TP manual". |
| **⇄** | Re-entry sisi itu di range yang sama. Lihat "Re-entry". |
| **✕** hijau di samping BUY | Membatalkan order pending BUY plan itu dan menutup posisi BUY yang terbuka. |
| **✕** merah di samping SELL | Sama untuk SELL. |
| **✕** gelap di ujung baris | Membatalkan seluruh plan dan menutup semua posisinya. |

Plan lain tidak pernah disentuh. Tombol ✕ tetap berfungsi saat ACTP HALTED. Setelah aksi massal (HENTIKAN SEMUA, Tutup semua / yang profit, Batalkan PO, tombol ✕ plan), log menampilkan "Took x.x s." (lama prosesnya).

Jika Anda mengklik di luar tabel Plan aktif saat TP baru sedang diketik di sebuah kotak, kotak itu kembali ke TP yang berlaku.

**Kotak timer** di samping baris status menunjukkan timeframe yang dipakai, sisa waktu sampai candle ditutup, dan nama plan. Angkanya berubah dari hijau ke kuning ke merah saat candle hampir ditutup. Kotak ini juga berjalan saat tidak ada plan aktif (lalu menampilkan timeframe yang akan dipakai plan berikutnya).

### TP manual

Ketik harga di kotak TP sebuah sisi plan lalu tekan **↻** (atau Enter). Hanya plan dan sisi itu yang berubah.

- Hanya **posisi terbuka** yang mendapat TP baru. Centang **Update TP juga mengubah order pending** agar order pending ikut berubah.
- TP **di dalam range** (atau di mana pun MetaTrader menolaknya) juga bisa. ACTP lalu memantau harga tiap detik dan, saat harga mencapai TP (BUY: bid ≥ TP, SELL: ask ≤ TP), menutup **posisi terbuka** sisi itu. Order pending dibiarkan. Level yang sedang dipantau ACTP ditampilkan berwarna oranye. Level itu tetap ada setelah ACTP ditutup dan dibuka lagi, dan hanya bekerja satu kali.
- Jika harga **sudah melewati** TP dan ada posisi terbuka, ACTP bertanya dulu, karena posisi akan langsung tertutup. Pertanyaan ini bisa dimatikan seperti popup lainnya.
- Jika **belum ada posisi terbuka**, TP disimpan dan dipantau, dan MT5 tidak diubah (centang kotak order pending agar TP juga dipasang di order pending). TP bekerja begitu posisi terbuka dan harga berada di TP.
- TP yang diatur manual menggantikan Auto-TP untuk sisi plan itu, kecuali **Auto-TP diprioritaskan** dicentang.

### Re-entry

Tombol **⇄** sebuah sisi membuka jendela kecil di sampingnya yang menampilkan range lama sisi itu beserta pip-nya, dengan dua pilihan:

- re-entry dengan **jumlah layer dan lot yang sama seperti sebelumnya**, atau
- re-entry dengan **jumlah layer dan lot saat ini** (kotak di jendela utama).

Order pending sisi itu dibatalkan, posisi terbukanya ditutup, dan layer dipasang lagi di range yang sama. Plan tetap memakai nama, Cut Loss, SL, dan TP-nya. Sisi lain dan plan lain tidak disentuh. ACTP bertanya "yakin?" dulu. Jendelanya menutup sendiri saat Anda memakai aplikasi lain. Saat Auto-TP hidup, jendela ini juga menampilkan harga tempat Auto-TP akan memindahkan TP ("TP ke proximal saat harga menyentuh ...").

### Auto-TP

**Auto-TP: saat harga [100] % di dalam range satu sisi, pindahkan TP ke proximal.** `proximal` adalah entry pertama (BUY: layer tertinggi, SELL: layer terendah); `distal` adalah entry terakhir, yang paling dekat dengan Cut Loss.

- **0%** adalah ujung proximal range sisi itu dan **100%** ujung distal. Kotaknya menerima 1 sampai 100 dan diingat; 100 berarti harga mencapai layer terakhir.
- ACTP mengikuti harga tiap detik (sisi BUY mengikuti ask, sisi SELL mengikuti bid) dan mengingat **titik terdalam** yang dicapai harga, jadi penurunan yang lalu kembali tetap dihitung. Ingatan ini disimpan, jadi memulai ulang ACTP tidak melupakannya. Arahkan kursor ke ⓘ di samping barisnya.
- Minimal satu posisi harus terbuka. TP semua order terbuka dan pending sisi itu lalu dipindah ke proximal. Satu kali per plan dan per sisi, setiap plan berdiri sendiri.
- Sisi yang TP-nya Anda atur manual (tombol ↻, atau menggesernya di MetaTrader) dibiarkan. Centang **Auto-TP diprioritaskan di atas update TP manual** dan Auto-TP tetap menggantikannya, dan ACTP berhenti memantau level yang Anda atur manual pada sisi itu.

Contoh: range BUY 3443.00 - 3439.87. Pada 70% harganya 3443.00 − 0.70 × 3.13 = 3440.81.

### Auto remove

**Hapus sisi PLAN yang tidak punya posisi terbuka dan [90] % di dalam range satu sisi.** Saat harga sudah sejauh itu di dalam range satu sisi (memakai ingatan titik terdalam yang sama dengan Auto-TP) dan sisi itu **tidak punya posisi terbuka lagi**, sisa order pending-nya dibatalkan. Contoh: 6 layer, Auto remove 80%. Harga turun, 4 layer terisi lalu tertutup (kena TP atau ditutup manual): 2 layer sisanya dibatalkan.

- Sisi yang masih punya posisi terbuka tidak pernah disentuh; ACTP menunggu sampai yang terakhir tertutup.
- Sisi yang harganya tidak pernah masuk cukup dalam tidak pernah disentuh.
- Sisi lain dan plan lain tidak disentuh.
- Mati secara bawaan; kotak persen menerima 1 sampai 100 dan diingat.

### Input Manual

Tab **Input Manual** memungkinkan Anda membuat plan tanpa pesan WhatsApp. Isi **range** (dua harga) untuk sisi yang Anda mau, SELL di atas dan BUY di bawah; sisi dengan range kosong tidak dipakai. Kosongkan **Cut loss**, **Stop loss**, atau **Take profit** untuk memakai nilai bawaan yang tampil di sebelah kotak:

| Harga | Nilai bawaan |
|---|---|
| Cut loss | 30% dari lebar range, di luar ujung jauh range |
| Stop loss | **SL Manual = x range** (Pengaturan, bawaan 3) kali lebar range, di luar ujung jauh |
| Take profit | Satu lebar range melewati layer teratas |

**Satu harga per sisi.** Alih-alih dua range, Anda bisa mengetik **satu harga di sisi SELL dan satu harga di sisi BUY** (di kotak mana pun) lalu menekan **tambah range PO buy dan sell**. Harga SELL adalah batas atas (sell distal) dan harga BUY adalah batas bawah (buy distal). TP kedua sisi tepat di tengah, dan ujung proximal tiap sisi berada di tengah antara ujung distal-nya dan TP. Dengan 0 = batas bawah dan 1 = batas atas, serta pengali Stop loss bawaan 3, levelnya: Stop loss -0.75, Cut loss -0.075, BUY distal 0, BUY proximal 0.25, TP 0.5, SELL proximal 0.75, SELL distal 1, Cut loss 1.075, Stop loss 1.75. Petunjuk abu-abu di samping kotak menampilkan range dan TP hasil hitungan sebelum Anda mengirim.

Layer dan lot diambil dari kotak **Jumlah layer** dan **MultiLayer**. Tab ini menulis teks `PLAN` biasa dan mengirimnya melalui pemeriksaan yang sama dengan pesan tempel, jadi Anda mendapat ringkasan dan popup konfirmasi yang sama.

<p align="center">
  <img src="docs\main-manual-screen.png" width="400" alt="Dasbor proyek">
</p>

### MultiLayer

Kotak **MultiLayer** di bawah kotak tempel memungkinkan Anda memilih ukuran setiap layer, dan berapa banyak layer, untuk plan yang Anda kirim berikutnya. Ketik satu angka per layer, misalnya `1, 1, 1, 1, 2, 2`. Setiap angka mengalikan **Lot** (lot dasar).

| Lot dasar | MultiLayer | Hasil |
|---|---|---|
| 0.06 | `1, 1, 1, 1, 2, 2` | 6 layer: 0.06 x 4, lalu 0.12 x 2 |
| 0.06 | `1, 1, 1, 2` | 4 layer: 0.06 x 3, lalu 0.12 x 1 |

<p align="center">
  <img src="docs/multilayer.gif" width="500" alt="Mengetik daftar MultiLayer dan melihat lot tiap layer">
</p>

> GIF ini menampilkan v1.0.2. Lihat [catatan rilis](https://github.com/ALtera21/ACTP/releases) untuk yang baru di v2.1.0.

- **Layer 1 adalah harga pertama yang dicapai pasar** (BUY: entry tertinggi, SELL: entry terendah). Lot yang lebih besar pada contoh di atas adalah layer terdalam.
- Daftar ini juga menentukan jumlah layer. ACTP menyebar sebanyak itu layer secara merata di area plan, berapa pun jumlah layer yang tertulis di pesan.
- **Biarkan kotak kosong** dan ACTP memakai kotak **Jumlah layer**: sebanyak itu layer, masing-masing satu lot dasar (atau **MultiLayer tersimpan** Anda untuk jumlah itu, lihat di bawah).
- Daftar yang sama dipakai untuk BUY dan SELL. Daftar ini diabaikan oleh pesan UPDATE dan CLOSE.
- Pakai 2 sampai 200 angka, masing-masing di atas 0 dan paling besar 10. Desimal seperti `1.5` boleh jika lot hasilnya kelipatan 0.01. Satu plan maksimal berisi 400 order.
- Ringkasan Kirim mencantumkan lot setiap layer dan totalnya per sisi. ACTP juga memeriksa lot terhadap minimum, kelipatan, dan maksimum broker sebelum apa pun dikirim.
- Kotak ini **tidak disimpan** dan **tidak dikosongkan setelah Kirim**. Saat terisi, baris oranye di bawahnya menampilkan lot yang akan dipakai.
- Jika plan terhenti (HALTED), Kirim pesan yang sama lagi dengan nilai MultiLayer yang **sama** untuk melanjutkannya. Nilai yang berbeda memulai plan baru.
- **Maks. layer di harga pasar per sisi** menghitung layer, bukan lot, jadi layer besar dihitung sama dengan layer kecil.

**MultiLayer tersimpan** (Pengaturan → **MultiLayer tersimpan...**): simpan sebuah daftar untuk suatu jumlah layer, misalnya 4 layer = `1, 1, 1, 2`. Setiap kali kotak MultiLayer kosong dan Jumlah layer adalah 4, daftar itu dipakai. Jumlah layer tanpa daftar tersimpan memakai 1x untuk setiap layer. Daftar yang diketik di kotak MultiLayer selalu menang.

### Plan terlambat

Jika Anda menempel plan setelah harga sudah bergerak masuk ke dalamnya, layer yang entry-nya sudah tercapai dibeli **di harga pasar**, bukan gagal. Ringkasan Kirim menunjukkan pembagiannya. Plan ditolak jika:
- lebih banyak layer daripada **Maks. layer di harga pasar per sisi** yang akan terisi di harga pasar, atau
- harga sudah melewati Cut Loss atau SL sisi itu.

### Cut Loss

Dengan **Auto cut loss** hidup, ACTP menutup satu sisi plan secara utuh (membatalkan pending, menutup yang terbuka) begitu aturan di bawah terpenuhi. Setiap plan menyimpan aturan dan timeframe saat plan itu dikirim, jadi mengubah pengaturan nanti tidak mengubah plan yang sudah aktif.

**Penutupan candle (bawaan).** Sebuah candle pada timeframe pilihan Anda harus **ditutup** melewati Cut Loss plan (BUY: di bawahnya, SELL: di atasnya). Hanya candle yang dibuka **setelah** plan dikirim yang dihitung, dan lonjakan harga yang kembali sebelum candle ditutup tidak berpengaruh. Pilih timeframe di kotak **Candle** (M1, M2, M3, M4, M5, M6, M10, M12, M15, M20, M30, H1). Kotak timer menghitung mundur sampai penutupan candle yang sedang berjalan. Hitung mundur mengikuti tick harga terakhir, jadi bisa meleset beberapa detik saat pasar sepi atau tutup.

**Timer lama (opsional).** Centang **Pakai timer lama** di Pengaturan untuk kembali ke aturan v1:
1. harga (BUY: bid, SELL: ask) bertahan melewati Cut Loss plan selama **waktu tahan** tanpa terputus, **dan**
2. dengan **SCL** hidup, harga juga berada di atau melewati garis Stop Cut Loss pada saat itu.

SCL berada di antara Cut Loss dan SL, pada persentase jalur yang dipilih dari Cut Loss ke SL. Jeda lebih dari 15 detik antar pemeriksaan harga mengulang timer dari awal.

Dengan auto cut loss **mati**, tidak ada yang menutup sisi secara otomatis: hanya SL/TP broker, HENTIKAN SEMUA, atau Anda.

**Cut Loss, Auto-TP, Auto remove, dan pemantau TP hanya berjalan selama jendela ACTP terbuka.** Menutupnya meminta konfirmasi, karena pemantauan berhenti. Order tetap memiliki SL/TP di broker.

### Konfirmasi popup

Pengaturan punya kotak merah **Konfigurasi popup (Pengaturan berbahaya)**. **Konfirmasi popup** adalah saklar utama dan **Atur tiap popup...** membuka satu kotak centang per aksi: Kirim plan, Batalkan PO buy / sell, HENTIKAN SEMUA, Tutup semua posisi, Tutup yang profit saja, tombol di tabel plan (batalkan satu sisi, batalkan plan, re-entry, Update TP saat harga sudah melewatinya), dan Keluar. Semuanya tercentang secara bawaan, artinya popup OK / Batal yang normal.

**Menghapus centang** sebuah kotak (atau kotak utama) lebih dulu meminta Anda mengetik kode 5 digit acak. Sesudah itu, aksi berjalan langsung **tanpa popup sama sekali**, jadi lakukan hanya jika Anda menerima risiko salah klik. Mencentang kembali tidak memerlukan kode.

### Bahasa dan tema

- **Bahasa:** Pengaturan → **Bahasa**: English atau Bahasa Indonesia. Berlaku setelah restart. Teks error rinci dari broker dan parser tetap berbahasa Inggris.
- **Tema:** tombol ☾ / ☀ di sebelah kanan baris Jumlah layer berganti antara terang dan gelap. Berlaku setelah restart.

### Logo

Logo di title bar dan taskbar diatur di `logo.py` (file gambar atau emoji). Untuk mengganti juga ikon `.exe`, lihat `make_icon.py` (petunjuk build ada di baris-baris pertamanya).

## 📋 Konfigurasi Trading

Atur ini di **Pengaturan...** (atau di jendela utama bila disebutkan). Nilai bawaan ditampilkan.

| Pengaturan | Bawaan | Boleh | Artinya |
|---|---|---|---|
| Simbol XAUUSD | `XAUUSDc` | Varian XAUUSD apa pun (`XAUUSD`, `XAUUSDm`, `XAUUSD.r`, ...) | Satu-satunya simbol yang akan di-trade ACTP. Pesan selalu menulis `XAUUSDr`; itu nama dari penyedia, bukan nama di broker Anda. |
| Lot (jendela utama) | 0.01 | 0.01 sampai 10, kelipatan 0.01 | Volume tiap layer (lot dasar yang dikalikan MultiLayer). Harus sesuai batas broker Anda. |
| Jumlah layer (jendela utama) | 6 | 2 sampai 200 | Jumlah layer plan berikutnya saat MultiLayer kosong. |
| Candle (jendela utama) | M1 | M1 sampai M6, M10, M12, M15, M20, M30, H1 | Timeframe candle untuk Cut Loss plan baru. |
| Auto-TP | Hidup | Hidup / Mati | Lihat "Auto-TP". Jendela utama. |
| Persen Auto-TP | 100 | 1 sampai 100 | Seberapa jauh harga harus masuk ke range satu sisi. Jendela utama. |
| Auto-TP diprioritaskan | Hidup | Hidup / Mati | Auto-TP juga menggantikan TP yang diatur manual. Jendela utama. |
| Update TP juga mengubah order pending | Hidup | Hidup / Mati | Jendela utama, di sebelah judul Plan aktif. |
| Auto remove | Mati | Hidup / Mati | Lihat "Auto remove". Jendela utama. |
| Persen Auto remove | 90 | 1 sampai 100 | Jendela utama. |
| Maks. layer di harga pasar per sisi | 3 | 0 sampai 200 | Lihat "Plan terlambat". |
| SL Manual = x range | 3 | 0.1 sampai 100 | Jarak Stop loss bawaan untuk tab Input Manual. |
| Auto cut loss | Hidup | Hidup / Mati | Lihat "Cut Loss". |
| Pakai timer lama | Mati | Hidup / Mati | Memakai aturan waktu-tahan + SCL, bukan candle. |
| Waktu tahan | 60 dtk | 10 sampai 3600 | Hanya timer lama: berapa lama pelanggaran Cut Loss harus bertahan. |
| SCL | Hidup | Hidup / Mati | Hanya timer lama: harga juga harus mencapai garis Stop Cut Loss. |
| Jarak SCL | 15 % | 5 sampai 95 | Hanya timer lama: seberapa jauh dari Cut Loss ke arah SL garis SCL berada. |
| Konfirmasi popup | Semua hidup | per aksi | Lihat "Konfirmasi popup". |
| Bahasa | English | English / Bahasa Indonesia | Bahasa jendela. |

Simbol Anda harus punya minimal 2 desimal harga.

<p align="center">
  <img src="docs\settings.png" width="400" alt="Jendela pengaturan">
</p>

> **Catatan:**
Ukuran lot yang tertulis di trading plan WhatsApp asli diabaikan. ACTP memakai volumenya sendiri yang sudah diatur.

## ⚠️ Catatan Penting

- ACTP tidak punya mode dry-run. Cobalah dulu di akun demo, satu salinan per akun, biarkan PC tetap menyala dan jendela tetap terbuka, periksa banner dan server, risiko ada pada Anda.
- Pastikan terminal MT5 Anda terhubung ke akun trading yang benar.
- Jangan pernah memakai rilis yang tidak terverifikasi untuk trading live.
- Penutupan dan pembatalan massal dikirim dengan 8 pekerja paralel. Baris "Took x.x s." di log memberi tahu berapa lama tiap proses.
- Trading mengandung risiko keuangan. Anda bertanggung jawab mengonfigurasi dan memantau lingkungan trading Anda.

## 🐛 Pemecahan Masalah

| Pesan | Yang perlu dilakukan |
|---|---|
| `Cannot start: ...` | Buka MetaTrader 5, login, hidupkan Algo Trading, lalu jalankan ACTP lagi. |
| `Cannot read your settings file` | Perbaiki atau hapus `%APPDATA%\ACTP\settings.json`. |
| `Status: HALTED` | Periksa MT5, tekan **Rekonsiliasi**, lalu Kirim pesan yang sama lagi untuk melanjutkan. Untuk menyingkirkan satu plan, pakai tombol **✕** gelapnya di tabel Plan aktif. |
| Windows memblokir aplikasi | More info → Run anyway (lihat Unduh). |
| This account is not a HEDGING account | Pakai akun hedging. Tanyakan ke broker Anda. |
| Logo tidak berubah | Restart ACTP. Logo emoji membutuhkan Pillow; pakai file PNG sebagai gantinya (lihat `logo.py`). |

**Log:** ACTP menulis satu file log per hari ke `%APPDATA%\ACTP\logs\actp.log` (disimpan 30 hari). Tempel `%APPDATA%\ACTP\logs` ke address bar File Explorer untuk membuka foldernya. Jika Anda melaporkan masalah, lampirkan baris yang relevan. Baris itu memuat nama server broker, tiket order, dan harga, jadi periksa dulu sebelum diposting di tempat umum.

Bug dan pertanyaan: buka issue di repositori GitHub.

## 📄 Lisensi

Lihat file LICENSE di repositori untuk ketentuan lisensi dan penggunaan.
