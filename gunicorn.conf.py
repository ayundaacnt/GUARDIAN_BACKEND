"""Konfigurasi Gunicorn untuk Guardian backend.

Catatan penting soal memori dan worker
--------------------------------------
Satu worker memuat SATU salinan penuh IndoBERTweet+BiLSTM (~1,2 GB commit,
terukur saat pengujian perangkat). Menambah worker melipatgandakan RAM itu
TANPA menambah throughput, karena ``GuardianClassifier.predict_batch``
diserialisasi oleh ``threading.Lock`` — hanya satu forward pass berjalan pada
satu waktu di dalam proses mana pun.

Karena itu: satu worker, beberapa thread. Thread menangani bagian yang
memang menunggu I/O (Firestore, Gemini, FCM) selagi inferensi mengantre.

Naikkan ``workers`` hanya kalau RAM server memang cukup untuk kelipatannya
(2 worker = ~2,5 GB) dan profiling menunjukkan CPU inferensi jadi leher botol.
"""

import os

# Platform seperti Cloud Run, Railway, dan Render menyuntikkan $PORT dan akan
# menolak container yang mendengarkan port lain.
bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"

workers = int(os.getenv("WEB_CONCURRENCY", "1"))
threads = int(os.getenv("WEB_THREADS", "4"))

# Jalur alert terpanjang = inferensi + Gemini + Firestore + FCM.
# Terukur 17 detik pada kasus terburuk; 120 memberi ruang aman.
timeout = 120
graceful_timeout = 30

# Koneksi menganggur dari load balancer jangan memutus request yang sedang jalan.
keepalive = 65

accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()
