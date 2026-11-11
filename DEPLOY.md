# Deploy Guardian Backend

Target: VPS Ubuntu, selalu hidup, tanpa Docker.
Kebutuhan minimum: **2 GB RAM**, 10 GB disk, Python 3.11+.

Backend ini satu-satunya bagian yang perlu di-hosting. Firestore, Firebase
Auth, FCM, dan Gemini sudah berjalan di cloud.

---

## 1. Berkas yang TIDAK ada di repo

Tiga berkas sengaja tidak ikut ter-commit dan harus disalin manual ke server:

| Berkas | Tujuan di server | Catatan |
|---|---|---|
| `best_weights.pt` (426 MB) | `backend/models/best_weights.pt` | Bobot IndoBERTweet+BiLSTM hasil fine-tune |
| `.env` | `backend/.env` | Berisi `GEMINI_API_KEY` — lihat `.env.example` |
| `serviceAccountKey.json` | sesuai `FIREBASE_CREDENTIALS_PATH` | **Kunci admin Firebase** |

`serviceAccountKey.json` melewati seluruh `firestore.rules`. Pemegangnya dapat
membaca semua dokumen `alerts` — yaitu isi pesan perundungan milik anak-anak
responden. Salin langsung ke server lewat `scp`, jangan lewat chat, dan jangan
pernah di-commit.

```bash
scp best_weights.pt       guardian@SERVER:/opt/guardian/backend/models/
scp .env                  guardian@SERVER:/opt/guardian/backend/
scp serviceAccountKey.json guardian@SERVER:/opt/guardian/backend/
```

---

## 2. Menyiapkan server

```bash
sudo adduser --system --group --home /opt/guardian guardian
sudo mkdir -p /opt/guardian/{backend,hf-cache}
sudo chown -R guardian:guardian /opt/guardian

sudo -u guardian git clone <URL-REPO> /tmp/src
sudo -u guardian cp -r /tmp/src/backend/. /opt/guardian/backend/

sudo -u guardian python3 -m venv /opt/guardian/venv
```

### Pasang dependensi — urutan ini penting

```bash
# torch CPU-only DULU. Tanpa langkah ini, pip menarik build CUDA ~2,5 GB
# dari PyPI yang tidak pernah dipakai (inferensi Guardian berjalan di CPU).
sudo -u guardian /opt/guardian/venv/bin/pip install \
    torch --index-url https://download.pytorch.org/whl/cpu

sudo -u guardian /opt/guardian/venv/bin/pip install \
    -r /opt/guardian/backend/requirements.txt
```

### Panaskan cache HuggingFace

Model dasar `indolem/indobertweet-base-uncased` (~400 MB) diunduh saat start
pertama. Pada pengujian, unduhan ini membuat cold start pertama **171 detik**.
Lakukan sekali di muka supaya restart berikutnya cepat:

```bash
sudo -u guardian HF_HOME=/opt/guardian/hf-cache \
  /opt/guardian/venv/bin/python -c \
  "from transformers import AutoModel, AutoTokenizer; \
   m='indolem/indobertweet-base-uncased'; \
   AutoTokenizer.from_pretrained(m); AutoModel.from_pretrained(m)"
```

Setelah cache terisi, boleh tambahkan `TRANSFORMERS_OFFLINE=1` di `.env` agar
server tidak pernah menunggu jaringan HuggingFace saat start.

---

## 3. Konfigurasi `.env`

Salin dari `.env.example`, lalu pastikan:

```
PRELOAD_MODEL=true          # wajib: model dimuat saat start, bukan saat request pertama
MODEL_DIR=models
MODEL_FILE=best_weights.pt
BULLY_THRESHOLD=0.6
FIREBASE_CREDENTIALS_PATH=/opt/guardian/backend/serviceAccountKey.json
GEMINI_API_KEY=...
LOG_LEVEL=INFO
```

`BULLY_THRESHOLD=0.6` ditetapkan dari pengujian perangkat: pada ambang 0.7,
perundungan terselubung (mis. sindiran soal fisik) hanya mencapai 0.62 dan
tidak pernah sampai ke orang tua.

---

## 4. systemd

```bash
sudo cp /opt/guardian/backend/deploy/guardian.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now guardian
sudo systemctl status guardian
sudo journalctl -u guardian -f
```

Tanda backend siap di log:

```
Classifier siap: best_weights.pt device=cpu
Classifier model preloaded and warmed up
```

---

## 5. HTTPS (wajib)

Aplikasi Android hanya mengizinkan HTTP polos pada build debug. Build yang
dibagikan ke responden harus menembak HTTPS.

```bash
sudo apt install nginx certbot python3-certbot-nginx
# server_name DOMAIN;  proxy_pass http://127.0.0.1:5000;
sudo certbot --nginx -d DOMAIN
```

Cek dari luar:

```bash
curl https://DOMAIN/health
# {"classifier":"indobertweet-bilstm","status":"ok","version":"0.1.0"}
```

---

## 6. Sisi aplikasi Android

`BACKEND_URL` dibaca saat kompilasi (`String.fromEnvironment`), bukan runtime.
APK wajib dibangun ulang:

```bash
flutter build apk --release --dart-define=BACKEND_URL=https://DOMAIN
```

---

## Catatan operasional

- **Jangan naikkan `workers`.** Tiap worker memuat salinan model sendiri
  (~1,2 GB) tanpa menambah throughput, karena inferensi diserialisasi mutex.
- Rate limiter memakai penyimpanan dalam memori; benar untuk satu instance,
  pecah kalau di-scale ke beberapa instance.
- Hindari platform yang menidurkan container saat idle. Scale-to-zero berarti
  orang tua menunggu pemuatan model sebelum alert pertama tiba.
- NLS di HP anak harus dikecualikan dari optimasi baterai dan diizinkan
  auto-start, khususnya pada perangkat vivo/Xiaomi/Oppo. Tanpa itu sistem
  membunuh prosesnya setelah beberapa jam dan alert berhenti diam-diam.
