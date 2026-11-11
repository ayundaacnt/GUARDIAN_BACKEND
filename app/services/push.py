"""FCM push notification delivery."""

import logging
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


def _collect_tokens(db, uid: str) -> List[str]:
    """Token FCM milik satu user: array users/{uid}.fcm_tokens + sub-koleksi
    devices/ (dipertahankan untuk kompatibilitas)."""
    user_ref = db.collection("users").document(uid)
    user_doc = user_ref.get()
    tokens = (
        list((user_doc.to_dict() or {}).get("fcm_tokens", []))
        if user_doc.exists
        else []
    )
    tokens += [
        (d.to_dict() or {}).get("fcm_token")
        for d in user_ref.collection("devices").stream()
    ]
    return list(dict.fromkeys(t for t in tokens if t))


def send_alert_push(
    uid_orangtua: str,
    alert_data: Dict[str, Any],
    exclude_tokens: Optional[Iterable[str]] = None,
) -> List[str]:
    """Kirim push ke SATU orang tua.

    Token yang ada di ``exclude_tokens`` dilewati (dipakai untuk membuang token
    HP anak yang nyasar, dan token yang sudah dikirimi lewat orang tua lain).
    Mengembalikan daftar token yang dituju.
    """
    from app.firebase_admin_client import get_firestore

    try:
        from firebase_admin import messaging
        from google.cloud import firestore
    except ImportError:
        logger.error("firebase_admin.messaging not available")
        return []

    db = get_firestore()
    user_ref = db.collection("users").document(uid_orangtua)

    exclude = set(exclude_tokens or [])
    tokens = [t for t in _collect_tokens(db, uid_orangtua) if t not in exclude]

    if not tokens:
        logger.info("No FCM tokens for parent uid; skipping push")
        return []

    notification = messaging.Notification(
        title="Peringatan Guardian",
        body=alert_data.get("penjelasan", "Pesan terdeteksi mengandung perundungan."),
    )

    data_payload = {
        "alertId": alert_data.get("dedup_key", ""),
        "dedup_key": alert_data.get("dedup_key", ""),
        "app_name": alert_data.get("aplikasi", ""),
        "sender": alert_data.get("pengirim", ""),
        "type": "bully_alert",
        # Hanya untuk pengukuran latensi di sisi orang tua (bukan isi pesan).
        "dibuat_at": str(alert_data.get("dibuat_at", "")),
    }

    failed_tokens = []

    for token in tokens:
        message = messaging.Message(
            notification=notification,
            data=data_payload,
            token=token,
        )
        try:
            messaging.send(message)
        except (messaging.UnregisteredError, messaging.SenderIdMismatchError):
            failed_tokens.append(token)
            logger.info("Removing invalid FCM token for parent uid")
        except Exception:
            logger.exception("FCM send failed for parent uid")

    # Clean up invalid tokens.
    if failed_tokens:
        user_ref.set({"fcm_tokens": firestore.ArrayRemove(failed_tokens)}, merge=True)
        _remove_invalid_tokens(db, uid_orangtua, failed_tokens)

    return tokens


def send_alert_push_to_parents(child_uid: str, alert_data: Dict[str, Any]) -> int:
    """Kirim push ke SEMUA orang tua yang berpasangan dengan anak ini.

    - Orang tua dicari dari koleksi ``pairings`` (uid_anak == child_uid).
    - Token yang juga tersimpan di akun anak dibuang (itu token HP anak).
    - Satu token tidak dikirimi dua kali walau ada di dua akun orang tua.

    Mengembalikan jumlah token yang dituju.
    """
    from app.firebase_admin_client import get_firestore
    from app.services.alert_store import parent_uids_for_child

    db = get_firestore()
    parent_uids = parent_uids_for_child(child_uid)
    if not parent_uids:
        logger.info("No active pairing for child uid; skipping push")
        return 0

    skip = set(_collect_tokens(db, child_uid))
    total = 0
    for parent_uid in parent_uids:
        sent = send_alert_push(parent_uid, alert_data, exclude_tokens=skip)
        skip.update(sent)
        total += len(sent)

    logger.info("Push sent: parents=%d tokens=%d", len(parent_uids), total)
    return total


def _remove_invalid_tokens(db, uid_orangtua: str, invalid_tokens: list) -> None:
    """Delete device documents whose FCM token is no longer valid."""
    devices = (
        db.collection("users")
        .document(uid_orangtua)
        .collection("devices")
        .stream()
    )
    for doc in devices:
        if (doc.to_dict() or {}).get("fcm_token") in invalid_tokens:
            doc.reference.delete()
            logger.info("Deleted invalid device token document")