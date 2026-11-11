"""Device heartbeat endpoint."""

import logging
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request

from app.auth import require_auth
from app.errors import GuardianError

logger = logging.getLogger(__name__)

devices_bp = Blueprint("devices", __name__, url_prefix="/api/v1/devices")


@devices_bp.route("/claim-token", methods=["POST"])
@require_auth
def claim_token():
    """Make the calling user the sole owner of this device's FCM token.

    POST /api/v1/devices/claim-token  {"token": "<fcm token>"}

    Satu HP bisa dipakai bergantian oleh akun anak dan orang tua. Token
    dihapus dari akun lain dulu, lalu disimpan di akun pemanggil, sehingga
    alert orang tua tidak lagi nyasar ke HP yang sekarang dipakai anak.
    """
    body = request.get_json(silent=True) or {}
    token = body.get("token")
    if not isinstance(token, str) or not token.strip() or len(token) > 4096:
        raise GuardianError("INVALID_INPUT", "token is required", 400)
    token = token.strip()

    from firebase_admin import firestore as fa_firestore

    from app.firebase_admin_client import get_firestore
    from app.services.alert_store import remove_tokens_everywhere

    remove_tokens_everywhere([token], except_uid=g.uid)
    get_firestore().collection("users").document(g.uid).set(
        {"fcm_tokens": fa_firestore.ArrayUnion([token])}, merge=True
    )

    # Nama profil disalin ke dokumen pairing di sini karena aturan Firestore
    # melarang satu pihak membaca users/{uid} pihak lain: tanpa salinan ini,
    # HP orang tua tidak punya sumber lain untuk nama anak (dan sebaliknya).
    # claim-token dipanggil sekali tiap app start, jadi nama yang diisi atau
    # diubah setelah pairing dibuat ikut menyusul dengan sendirinya.
    # Gagal menyalin tidak boleh menggagalkan klaim token (tugas utama rute ini).
    from app.services.pairing_service import sync_child_name, sync_parent_name

    try:
        if g.role == "parent":
            sync_parent_name(g.uid)
        else:
            sync_child_name(g.uid)
    except Exception:
        logger.exception("claim-token: sinkronisasi nama gagal (token tetap diklaim)")

    logger.info("FCM token claimed by uid=%s", g.uid)
    return jsonify(status="ok"), 200


@devices_bp.route("/heartbeat", methods=["POST"])
@require_auth
def heartbeat():
    """Record a device heartbeat.

    POST /api/v1/devices/heartbeat

    Updates ``last_seen`` and ``protection_active`` on the active pairing
    document for the calling child device.
    """
    body = request.get_json(silent=True) or {}
    protection_active = body.get("protection_active", True)

    from app.services.pairing_service import get_active_pairing_for_child
    from app.firebase_admin_client import get_firestore

    pairing = get_active_pairing_for_child(uid_anak=g.uid)
    if pairing is None:
        raise GuardianError("NOT_FOUND", "No active pairing for this device", 404)

    db = get_firestore()
    pairing_ref = db.collection("pairings").document(pairing["pairing_id"])
    pairing_ref.update(
        {
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "protection_active": bool(protection_active),
        }
    )

    logger.info("Heartbeat recorded for pairing=%s", pairing["pairing_id"])

    return jsonify(status="ok"), 200
