# Guardian Backend

Backend Flask untuk Guardian — deteksi cyberbullying bahasa Indonesia pada
pesan chat, dengan notifikasi ke orang tua.

**Untuk men-deploy, baca [DEPLOY.md](DEPLOY.md).**

## Alur

```
NLS Android (Kotlin)
   │  satu bubble chat
   ▼
POST /api/v1/classify        (Bearer: Firebase ID token anak)
   │
   ├─ pairing anak aktif?  tidak → drop
   ├─ paket aplikasi dipantau?  tidak → drop
   ▼
IndoBERTweet + BiLSTM  →  p_bully
   │
   ├─ p_bully < AMBANG → selesai. Teks TIDAK disimpan, TIDAK dicatat.
   ▼
Gemini menyusun penjelasan  →  Firestore `alerts`  →  FCM ke orang tua
```

Pesan non-bully dibuang seketika. Hanya pesan yang melewati ambang yang
pernah menyentuh penyimpanan atau LLM.

## Endpoint

| Metode | Jalur | Keterangan |
|---|---|---|
| GET | `/health` | Status layanan |
| POST | `/api/v1/classify` | Klasifikasi satu bubble, jalur utama |
| POST | `/api/v1/pairing/...` | Pembuatan & penukaran kode pairing |
| POST | `/api/v1/devices/claim-token` | Kepemilikan token FCM per perangkat |
| POST | `/api/v1/devices/heartbeat` | Denyut perangkat anak |
| POST | `/api/v1/profile/sync-name` | Salin nama ke dokumen pairing |
| POST | `/api/v1/cek` | Diagnostik: p_bully mentah. Mati kecuali `DEBUG_INFERENCE=true` |

Semua endpoint selain `/health` menuntut token Firebase dan kena rate limit.

## Model

IndoBERTweet (`indolem/indobertweet-base-uncased`) dengan kepala BiLSTM,
fine-tune penuh, inferensi fp32 di CPU (~60–100 ms per pesan).

Preprocessing di `app/services/preprocessing.py` **harus identik** dengan
pipeline saat training — termasuk de-leet (`b0d0h` → `bodoh`), un-spacing
(`b o d o h` → `bodoh`), dan peredaman huruf berulang. Mengubahnya tanpa
melatih ulang menurunkan akurasi tanpa pesan error apa pun.

Bobot (`best_weights.pt`, 426 MB) tidak ada di repo — lihat
[models/README.md](models/README.md).

## Konfigurasi

Semua lewat variabel lingkungan; contoh lengkap di `.env.example`.
Yang paling sering disetel:

| Variabel | Default | Arti |
|---|---|---|
| `BULLY_THRESHOLD` | `0.6` | Ambang keputusan alert |
| `MODEL_FILE` | `best_weights.pt` | Nama berkas bobot di `MODEL_DIR` |
| `PRELOAD_MODEL` | `false` | Setel `true` di produksi |
| `DEBUG_INFERENCE` | `false` | Membuka `/api/v1/cek` |

## Jalan lokal

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
cp .env.example .env     # isi GEMINI_API_KEY & FIREBASE_CREDENTIALS_PATH
flask run
```
