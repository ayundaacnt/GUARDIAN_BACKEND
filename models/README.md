# Model Weights

Letakkan bobot hasil training (IndoBERTweet + BiLSTM) di sini:

```
models/
  best_weights.pt     # state dict fine-tune penuh IndoBERTweet + kepala BiLSTM (~426 MB)
```

Nama file dibaca dari `MODEL_FILE` di `.env` (default `best_weights.pt`), foldernya
dari `MODEL_DIR` (default `models`). File `*.pt` di-ignore git.

## Base model

Arsitektur, config, dan tokenizer diambil dari `indolem/indobertweet-base-uncased`
(HuggingFace) saat classifier pertama dimuat — **butuh internet pada run pertama**.
Setelah itu tersimpan di cache HuggingFace lokal.

Untuk deployment offline, bundel cache-nya dan arahkan ke sana:

```
HF_HOME=/path/ke/hf-cache
TRANSFORMERS_OFFLINE=1
```

## Catatan penting

- Preprocessing wajib identik dengan training: lihat `app/services/preprocessing.py`.
  Mengubahnya tanpa melatih ulang model menurunkan akurasi tanpa gejala error.
- fp32 saja. Jangan pakai fp16 — di GPU lama justru lebih lambat, dan tidak
  dipakai saat training.
- Model dimuat sekali saat server start (`PRELOAD_MODEL=true`), bukan per request.
  Cold start ~9,5 detik.
- `best.pt` (model lama, Exp 15 DAPT) **tidak kompatibel** dengan preprocessing
  sekarang karena dilatih tanpa `normalize_obfuscation`. Jangan dipakai lagi.
