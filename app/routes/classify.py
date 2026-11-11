"""Classification endpoint."""

import logging
import time
from datetime import datetime, timezone

from flask import Blueprint, current_app, g, jsonify, request
from pydantic import BaseModel, Field, field_validator

from app import limiter
from app.auth import require_auth
from app.errors import GuardianError

logger = logging.getLogger(__name__)

classify_bp = Blueprint("classify", __name__, url_prefix="/api/v1")

_MAX_TEXT_LENGTH = 2000


# --------------- request schema ---------------

class ClassifyRequest(BaseModel):
    dedup_key: str = Field(..., min_length=1)
    app_package: str = Field(..., min_length=1)
    app_name: str = Field(..., min_length=1)
    sender: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    posted_at: str = Field(..., min_length=1)

    @field_validator("text")
    @classmethod
    def truncate_text(cls, v: str) -> str:
        return v[:_MAX_TEXT_LENGTH]


# --------------- endpoint ---------------

@classify_bp.route("/classify", methods=["POST"])
@require_auth
@limiter.limit(lambda: current_app.config["GUARDIAN"].RATE_LIMIT_CLASSIFY)
def classify():
    """Classify a chat message.

    POST /api/v1/classify
    """
    t_start = time.perf_counter()
    config = current_app.config["GUARDIAN"]
    uid = g.uid
    key = None  # untuk log

    # ---- parse & validate ----
    body = request.get_json(silent=True)
    if not body:
        raise GuardianError("INVALID_INPUT", "Request body must be JSON", 400)

    try:
        payload = ClassifyRequest(**body)
    except Exception as exc:
        logger.warning("classify: input tidak valid uid=%s err=%s", uid, exc)
        raise GuardianError("INVALID_INPUT", str(exc), 400)

    key = payload.dedup_key[:8]

    # ---- check pairing exists ----
    from app.services.pairing_service import get_active_pairing_for_child

    pairing = get_active_pairing_for_child(uid_anak=uid)
    if pairing is None:
        logger.warning(
            "classify[%s]: DROP no_active_pairing uid_anak=%s", key, uid
        )
        return jsonify(action="drop", reason="no_active_pairing"), 200

    # ---- check monitored package ----
    if payload.app_package not in config.MONITORED_PACKAGES:
        logger.warning(
            "classify[%s]: DROP unmonitored_package pkg=%s (daftar backend=%s)",
            key, payload.app_package, config.MONITORED_PACKAGES,
        )
        return jsonify(action="drop", reason="unmonitored_package"), 200

    # ---- classify ----
    from app.services.classifier import get_classifier

    t_pre = time.perf_counter()
    classifier = get_classifier(config)
    prediction = classifier.predict(payload.text)  # preprocessing ada di classifier
    t_clf = time.perf_counter()

    label = prediction["label"]
    prob = float(prediction["prob_bully"])
    logger.info(
        "classify[%s]: pkg=%s model=indobertweet-bilstm label=%s prob_bully=%.3f threshold=%.2f len=%d",
        key, payload.app_package,
        label, prob, config.BULLY_THRESHOLD, len(payload.text),
    )

    # ---- non-bully: return immediately, teks tidak dicatat ----
    if label != "bully" or prob < config.BULLY_THRESHOLD:
        logger.info(
            "latency[%s]: path=ok pre_ms=%.0f classify_ms=%.0f total_ms=%.0f",
            key, (t_pre - t_start) * 1000, (t_clf - t_pre) * 1000,
            (time.perf_counter() - t_start) * 1000,
        )
        return jsonify(
            action="ok",
            reason="not_bully_or_below_threshold",
            label=label,
            prob_bully=round(prob, 3),
        ), 200

    # ---- bully path: explain + store + push ----
    from app.services.explainer import explain
    from app.services.alert_store import alert_exists, write_alert
    from app.services.push import send_alert_push_to_parents

    # Pesan yang sudah pernah di-alert (dikirim ulang oleh NLS) tidak perlu
    # dijelaskan Gemini lagi: hemat kuota dan waktu. Responsnya tetap sama.
    if alert_exists(payload.dedup_key):
        logger.info(
            "classify[%s]: alert duplikat, lewati explain/push "
            "latency[%s]: path=duplicate total_ms=%.0f",
            key, key, (time.perf_counter() - t_start) * 1000,
        )
        return jsonify(action="alerted"), 200

    # Gagal menjelaskan (mis. kuota Gemini) tidak boleh menggagalkan alert.
    t_exp0 = time.perf_counter()
    try:
        explanation = explain(
            text=payload.text,
            label=label,
            confidence=prob,
            config=config,
        )
    except Exception:
        logger.exception("classify[%s]: explain gagal, lanjut tanpa penjelasan", key)
        explanation = {}
    t_exp = time.perf_counter()

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # Field ini hanya orang tua "utama" dari pairing yang ditemukan. Alert
    # dibaca semua orang tua lewat dokumen pairing (lihat firestore.rules),
    # dan push dikirim ke SEMUA orang tua oleh send_alert_push_to_parents.
    uid_orangtua = pairing.get("uid_orangtua", "")
    if not uid_orangtua:
        logger.warning(
            "classify[%s]: pairing tidak punya uid_orangtua, alert tidak akan terbaca ortu",
            key,
        )

    alert_data = {
        # Nama field mengikuti spec Firestore yang dibaca app (Indonesia).
        "dedup_key": payload.dedup_key,
        "uid_anak": uid,
        "uid_orangtua": uid_orangtua,
        "teks": payload.text,
        "label": label,
        "confidence": prob,
        "aplikasi": payload.app_name,
        "paket_aplikasi": payload.app_package,
        "pengirim": payload.sender,
        "waktu": _posted_at_ms(payload.posted_at, default=now_ms),
        "dibuat_at": now_ms,
        "penjelasan": explanation.get("penjelasan", ""),
        "kata_kunci": explanation.get("kata_kunci", []),
        "status_tinjau": "belum",
    }

    # Kalau penulisan gagal, biarkan error naik (jangan balas 200 palsu).
    created = write_alert(dedup_key=payload.dedup_key, data=alert_data)
    t_write = time.perf_counter()
    logger.info(
        "classify[%s]: alert %s (uid_orangtua utama=%s)",
        key, "ditulis" if created else "duplikat", uid_orangtua,
    )

    # Push hanya untuk alert baru, ke SEMUA orang tua anak ini.
    # Push gagal tidak membatalkan alert yang sudah tersimpan.
    if created:
        try:
            send_alert_push_to_parents(child_uid=uid, alert_data=alert_data)
        except Exception:
            logger.exception("classify[%s]: push gagal (alert tetap tersimpan)", key)
    t_push = time.perf_counter()

    logger.info(
        "latency[%s]: path=alerted pre_ms=%.0f classify_ms=%.0f explain_ms=%.0f "
        "write_ms=%.0f push_ms=%.0f total_ms=%.0f nls_to_backend_ms=%d explain_ok=%s",
        key,
        (t_pre - t_start) * 1000,
        (t_clf - t_pre) * 1000,
        (t_exp - t_exp0) * 1000,
        (t_write - t_exp) * 1000,
        (t_push - t_write) * 1000,
        (t_push - t_start) * 1000,
        now_ms - alert_data["waktu"],
        explanation.get("sumber") == "gemini",
    )

    return jsonify(action="alerted"), 200


# --------------- helpers ---------------

def _posted_at_ms(value: str, default: int) -> int:
    """Convert an ISO-8601 timestamp to epoch milliseconds (fallback: default)."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return default