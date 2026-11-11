"""Configuration loaded from environment variables."""

import os
from typing import List


class Config:
    """Application configuration read from environment variables."""

    def __init__(self) -> None:
        self.FIREBASE_CREDENTIALS_PATH: str = os.getenv(
            "FIREBASE_CREDENTIALS_PATH", ""
        )
        self.GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
        self.GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite")

        self.MODEL_DIR: str = os.getenv("MODEL_DIR", "models")
        self.MODEL_FILE: str = os.getenv("MODEL_FILE", "best_weights.pt")
        # Muat model di latar belakang saat server start, supaya request
        # pertama tidak menanggung ~8 detik pemuatan model.
        self.PRELOAD_MODEL: bool = os.getenv("PRELOAD_MODEL", "false").lower() in (
            "1", "true", "yes",
        )
        # Endpoint diagnostik /api/v1/cek (probabilitas mentah). Mati di produksi.
        self.DEBUG_INFERENCE: bool = os.getenv("DEBUG_INFERENCE", "false").lower() in (
            "1", "true", "yes",
        )
        # Prevalensi bully di pesan nyata hanya ~1-2%, jadi ambang 0.5
        # menghasilkan banyak alert palsu dan orang tua berhenti membaca
        # notifikasi. Tapi 0.7 terlalu ketat: pada uji perangkat 2026-11-10,
        # perundungan terselubung ("Baju lo bagus banget deh, pinter ya pilih
        # model yang bisa nutupin perut gendut") hanya mencapai 0.623 dan ikut
        # terbuang, padahal model sudah benar melabelinya bully. Sindiran dan
        # sarkasme cenderung berskor menengah, bukan ekstrem.
        self.BULLY_THRESHOLD: float = float(os.getenv("BULLY_THRESHOLD", "0.6"))

        raw_packages = os.getenv(
            "MONITORED_PACKAGES",
            "com.whatsapp,com.whatsapp.w4b,com.instagram.android,"
            "org.telegram.messenger,org.telegram.messenger.web,"
            "com.zhiliaoapp.musically,com.ss.android.ugc.trill",
        )
        self.MONITORED_PACKAGES: List[str] = [
            p.strip() for p in raw_packages.split(",") if p.strip()
        ]

        self.RATE_LIMIT_CLASSIFY: str = os.getenv("RATE_LIMIT_CLASSIFY", "30/minute")
        # Kode pairing hanya 6 digit: batasi tebakan per akun dan per IP.
        self.RATE_LIMIT_PAIRING_UID: str = os.getenv(
            "RATE_LIMIT_PAIRING_UID", "10/minute;60/hour"
        )
        self.RATE_LIMIT_PAIRING_IP: str = os.getenv(
            "RATE_LIMIT_PAIRING_IP", "30/minute;200/hour"
        )
        self.LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
