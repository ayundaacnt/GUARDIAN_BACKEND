"""Firestore alert storage + lookup penerima push."""

import logging
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


def alert_exists(dedup_key: str) -> bool:
    """True bila alert dengan dedup_key ini sudah tersimpan.

    Dipakai untuk melewati penjelasan Gemini pada pesan yang dikirim ulang
    (mis. NLS dinyalakan ulang). write_alert tetap jadi penjaga akhir kalau
    dua request identik masuk bersamaan.
    """
    from app.firebase_admin_client import get_firestore

    return get_firestore().collection("alerts").document(dedup_key).get().exists


def write_alert(dedup_key: str, data: Dict[str, Any]) -> bool:
    """Tulis dokumen alert ke Firestore dengan dedup_key sebagai ID dokumen.

    Returns:
        True  -> alert BARU dibuat (boleh kirim push).
        False -> alert duplikat (jangan kirim push lagi).
    """
    from app.firebase_admin_client import get_firestore

    db = get_firestore()
    doc_ref = db.collection("alerts").document(dedup_key)

    try:
        doc_ref.create(data)
        logger.info("Alert stored: dedup_key=%s", dedup_key)
        return True
    except Exception as exc:
        exc_name = type(exc).__name__
        if "Conflict" in exc_name or "AlreadyExists" in exc_name:
            logger.info("Duplicate alert ignored: dedup_key=%s", dedup_key)
            return False
        logger.exception("Failed to store alert: dedup_key=%s", dedup_key)
        raise


def parent_uids_for_child(child_uid: str) -> List[str]:
    """UID semua orang tua dengan pairing aktif ke anak ini (maks. 2)."""
    from app.firebase_admin_client import get_firestore

    db = get_firestore()
    uids: List[str] = []
    for snap in db.collection("pairings").where("uid_anak", "==", child_uid).stream():
        d = snap.to_dict() or {}
        parent_uid = d.get("uid_orangtua")
        if d.get("status", "active") == "active" and parent_uid:
            if parent_uid not in uids:
                uids.append(parent_uid)
    return uids


def _tokens_of(db, uid: str) -> List[str]:
    snap = db.collection("users").document(uid).get()
    data = snap.to_dict() or {}
    return [t for t in data.get("fcm_tokens", []) if isinstance(t, str) and t]


def parent_tokens_for_child(child_uid: str) -> List[str]:
    """Token FCM semua orang tua anak, tanpa duplikat.

    Token yang juga tersimpan di dokumen anak sendiri dibuang: itu pasti
    token HP anak yang nyasar ke akun orang tua.
    """
    from app.firebase_admin_client import get_firestore

    db = get_firestore()
    child_tokens = set(_tokens_of(db, child_uid))

    parent_uids = parent_uids_for_child(child_uid)
    tokens: List[str] = []
    for parent_uid in parent_uids:
        for t in _tokens_of(db, parent_uid):
            if t not in child_tokens and t not in tokens:
                tokens.append(t)

    logger.info(
        "Push recipients: child=%s parents=%d tokens=%d",
        child_uid, len(parent_uids), len(tokens),
    )
    return tokens


def remove_tokens_everywhere(
    tokens: Iterable[str], except_uid: Optional[str] = None
) -> None:
    """Hapus token dari semua dokumen user yang menyimpannya.

    ``except_uid`` dilewati (pemilik token yang sah, mis. saat klaim token).
    """
    from firebase_admin import firestore as fa_firestore

    from app.firebase_admin_client import get_firestore

    db = get_firestore()
    for token in tokens:
        try:
            for snap in (
                db.collection("users")
                .where("fcm_tokens", "array_contains", token)
                .stream()
            ):
                if snap.id == except_uid:
                    continue
                snap.reference.update(
                    {"fcm_tokens": fa_firestore.ArrayRemove([token])}
                )
                logger.info("Removed stale token from users/%s", snap.id)
        except Exception:
            logger.exception("Failed to remove stale token")