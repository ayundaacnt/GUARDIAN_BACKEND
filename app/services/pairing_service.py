"""Pairing lifecycle management.

Semua penulisan ke ``pairing_codes`` / ``pairings`` / ``users.pairing_count``
terjadi di sini lewat Admin SDK. Klien tidak punya akses tulis ke koleksi
tersebut (lihat firebase/firestore.rules), sehingga batas percobaan, masa
berlaku kode, dan batas orang tua per anak ditegakkan di server.

Skema dokumen mengikuti model Flutter (PairingCode / Pairing):

- ``pairing_codes/{6 digit}``: code, childUid, expiresAt (ms), createdAt (ms),
  childName, childPhoto, expires_at_ts (Timestamp, untuk TTL policy)
- ``pairings/{uidAnak}_{uidOrtu}``: lihat :func:`redeem_code`

Functions
- create_pairing_code    -- anak membuat kode baru
- cancel_code            -- anak membatalkan kodenya
- validate_code          -- pratinjau kode (tanpa efek samping)
- redeem_code            -- orang tua menebus kode, membuat pairing (atomik)
- revoke_pairing         -- anak atau orang tua memutus pairing (atomik)
- sync_parent_name       -- salin nama orang tua ke pairing-nya
- sync_child_name        -- salin nama anak ke pairing-nya
- get_active_pairing_for_child  -- pairing aktif seorang anak
"""

import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CODE_LENGTH = 6
CODE_TTL_MS = 5 * 60 * 1000  # harus sama dengan AppConstants.codeExpiryMinutes
MAX_PARENTS_PER_CHILD = 2
_CODE_ATTEMPTS = 8


# --------------- helpers ---------------

def generate_code(length: int = CODE_LENGTH) -> str:
    """Kode numerik acak (CSPRNG), sama dengan format yang dipindai/diketik."""
    return "".join(secrets.choice("0123456789") for _ in range(length))


def _now_ms() -> int:
    return int(time.time() * 1000)


def _is_code_format(code: str) -> bool:
    return len(code) == CODE_LENGTH and code.isascii() and code.isdigit()


def _run_in_transaction(db, fn):
    """Jalankan ``fn(transaction)`` sebagai transaksi Firestore (dengan retry)."""
    from firebase_admin import firestore as fa_firestore

    return fa_firestore.transactional(fn)(db.transaction())


def _increment(n: int):
    from firebase_admin import firestore as fa_firestore

    return fa_firestore.Increment(n)


def pairing_id_of(uid_anak: str, uid_orangtua: str) -> str:
    return f"{uid_anak}_{uid_orangtua}"


def _display_name(user_data: Dict[str, Any]) -> str:
    return (user_data.get("nama_lengkap") or user_data.get("display_name") or "").strip()


def _read_code(code_snap, now_ms: int) -> Dict[str, Any]:
    """Validasi dokumen kode; lempar GuardianError bila tidak bisa dipakai."""
    from app.errors import GuardianError

    if not code_snap.exists:
        raise GuardianError("INVALID_CODE", "Kode tidak ditemukan atau sudah dipakai", 404)
    data = code_snap.to_dict() or {}
    if int(data.get("expiresAt", 0)) <= now_ms:
        raise GuardianError("EXPIRED_CODE", "Kode sudah kedaluwarsa", 400)
    if not data.get("childUid"):
        raise GuardianError("INVALID_CODE", "Kode tidak valid", 400)
    return data


def count_active_parents(db, uid_anak: str, transaction=None) -> int:
    """Jumlah pairing aktif seorang anak (sumber kebenaran batas orang tua)."""
    query = db.collection("pairings").where("uid_anak", "==", uid_anak)
    snaps = query.stream(transaction=transaction) if transaction else query.stream()
    return sum(1 for s in snaps if (s.to_dict() or {}).get("status", "active") == "active")


# --------------- public API ---------------

def create_pairing_code(uid_anak: str) -> Dict[str, Any]:
    """Buat (dan simpan) kode pairing baru untuk anak.

    Kode lama anak ini dihapus lebih dulu. Ditolak bila anak sudah punya
    :data:`MAX_PARENTS_PER_CHILD` orang tua.

    Returns ``{"code", "created_at", "expires_at"}`` (waktu dalam epoch ms).
    """
    from google.api_core.exceptions import AlreadyExists

    from app.errors import GuardianError
    from app.firebase_admin_client import get_firestore

    db = get_firestore()

    if count_active_parents(db, uid_anak) >= MAX_PARENTS_PER_CHILD:
        raise GuardianError(
            "PAIRING_LIMIT",
            f"Perangkat ini sudah terhubung dengan {MAX_PARENTS_PER_CHILD} orang tua.",
            409,
        )

    codes = db.collection("pairing_codes")
    for old in codes.where("childUid", "==", uid_anak).stream():
        old.reference.delete()

    child = db.collection("users").document(uid_anak).get()
    child_data = (child.to_dict() or {}) if child.exists else {}

    now_ms = _now_ms()
    expires_ms = now_ms + CODE_TTL_MS
    for _ in range(_CODE_ATTEMPTS):
        code = generate_code()
        ref = codes.document(code)
        doc: Dict[str, Any] = {
            "code": code,
            "childUid": uid_anak,
            "createdAt": now_ms,
            "expiresAt": expires_ms,
            "expires_at_ts": datetime.fromtimestamp(expires_ms / 1000, tz=timezone.utc),
        }
        if child_data.get("display_name"):
            doc["childName"] = child_data["display_name"]
        if child_data.get("photo_url"):
            doc["childPhoto"] = child_data["photo_url"]
        try:
            ref.create(doc)
        except AlreadyExists:
            # Bentrok dengan kode orang lain: pakai ulang hanya bila sudah kedaluwarsa.
            existing = ref.get().to_dict() or {}
            if int(existing.get("expiresAt", 0)) > now_ms:
                continue
            ref.delete()
            try:
                ref.create(doc)
            except AlreadyExists:
                continue
        logger.info("Pairing code created")
        return {"code": code, "created_at": now_ms, "expires_at": expires_ms}

    raise GuardianError("CODE_GENERATION_FAILED", "Gagal membuat kode unik. Coba lagi.", 503)


def cancel_code(uid_anak: str) -> None:
    """Hapus semua kode milik anak ini (idempoten)."""
    from app.firebase_admin_client import get_firestore

    db = get_firestore()
    for doc in db.collection("pairing_codes").where("childUid", "==", uid_anak).stream():
        doc.reference.delete()
    logger.info("Pairing codes cancelled")


def validate_code(pairing_code: str) -> Dict[str, Any]:
    """Pratinjau kode untuk orang tua. Tidak mengubah apa pun.

    Melempar GuardianError bila kode salah format / tidak ada / kedaluwarsa.
    """
    from app.errors import GuardianError
    from app.firebase_admin_client import get_firestore

    if not _is_code_format(pairing_code):
        raise GuardianError("INVALID_CODE", "Format kode tidak valid", 400)

    db = get_firestore()
    data = _read_code(db.collection("pairing_codes").document(pairing_code).get(), _now_ms())
    return {
        "valid": True,
        "code": pairing_code,
        "child_uid": data["childUid"],
        "child_name": data.get("childName", ""),
        "child_photo": data.get("childPhoto", ""),
        "expires_at": int(data["expiresAt"]),
        "created_at": int(data.get("createdAt", 0)) or None,
    }


def redeem_code(pairing_code: str, uid_orangtua: str) -> Dict[str, Any]:
    """Tebus kode: buat pairing aktif, naikkan pairing_count, hapus kode.

    Semuanya satu transaksi, jadi kode benar-benar sekali pakai walau ditebus
    bersamaan, dan batas :data:`MAX_PARENTS_PER_CHILD` tidak bisa dilewati.
    Mengembalikan dokumen pairing (field Firestore) ditambah ``id``.
    """
    from app.errors import GuardianError
    from app.firebase_admin_client import get_firestore

    if not _is_code_format(pairing_code):
        raise GuardianError("INVALID_CODE", "Format kode tidak valid", 400)

    db = get_firestore()
    code_ref = db.collection("pairing_codes").document(pairing_code)
    users = db.collection("users")
    pairings = db.collection("pairings")

    def txn(tx):
        now_ms = _now_ms()
        # Semua baca dulu, baru tulis (aturan transaksi Firestore).
        code = _read_code(code_ref.get(transaction=tx), now_ms)
        uid_anak = code["childUid"]
        if uid_anak == uid_orangtua:
            raise GuardianError("SELF_PAIRING", "Tidak bisa memasangkan dengan akun sendiri", 400)

        pairing_ref = pairings.document(pairing_id_of(uid_anak, uid_orangtua))
        if pairing_ref.get(transaction=tx).exists:
            raise GuardianError("ALREADY_PAIRED", "Anda sudah terhubung dengan anak ini", 409)
        if count_active_parents(db, uid_anak, transaction=tx) >= MAX_PARENTS_PER_CHILD:
            raise GuardianError(
                "PAIRING_LIMIT",
                f"Anak ini sudah terhubung dengan {MAX_PARENTS_PER_CHILD} orang tua.",
                409,
            )

        parent = users.document(uid_orangtua).get(transaction=tx)
        parent_data = (parent.to_dict() or {}) if parent.exists else {}
        parent_name = _display_name(parent_data)
        parent_wa = _parent_wa(parent_data)

        # Masih di fase baca (transaksi Firestore melarang baca setelah tulis).
        child = users.document(uid_anak).get(transaction=tx)
        child_data = (child.to_dict() or {}) if child.exists else {}

        data: Dict[str, Any] = {
            "uid_anak": uid_anak,
            "uid_orangtua": uid_orangtua,
            "status": "active",
            "consent_anak": True,
            "consent_ortu": True,
            "paired_at": now_ms,
            "nama_perangkat": "Perangkat Anak",
            "last_seen": now_ms,
            "protection_active": False,
            "code": pairing_code,
        }
        if parent_name:
            data["nama_ortu"] = parent_name
        if parent_wa:
            data["wa_ortu"] = parent_wa
        # Nama anak dibaca SEGAR dari users/{uid_anak}, sama seperti nama orang
        # tua di atas. ``code["childName"]`` hanyalah salinan saat kode dibuat:
        # kalau anak belum mengisi profil waktu itu, field-nya tidak pernah
        # lahir dan nama di HP orang tua kosong selamanya.
        child_name = _display_name(child_data) or code.get("childName") or ""
        if child_name:
            data["nama_anak"] = child_name
        if code.get("childPhoto"):
            data["foto_anak"] = code["childPhoto"]

        tx.set(pairing_ref, data)
        tx.set(users.document(uid_anak), {"pairing_count": _increment(1)}, merge=True)
        tx.set(users.document(uid_orangtua), {"pairing_count": _increment(1)}, merge=True)
        tx.delete(code_ref)
        return {"id": pairing_ref.id, **data}

    result = _run_in_transaction(db, txn)
    logger.info("Pairing created: pairing_id=%s", result["id"])
    return result


def revoke_pairing(pairing_id: str, requester_uid: str) -> None:
    """Putus pairing (hapus dokumen, turunkan pairing_count keduanya).

    Idempoten: pairing yang sudah tidak ada dianggap berhasil. Hanya anak
    atau orang tua dalam pairing itu yang boleh memutus.
    """
    from app.errors import GuardianError
    from app.firebase_admin_client import get_firestore

    db = get_firestore()
    ref = db.collection("pairings").document(pairing_id)
    users = db.collection("users")

    def txn(tx):
        snap = ref.get(transaction=tx)
        if not snap.exists:
            return
        data = snap.to_dict() or {}
        uid_anak = data.get("uid_anak", "")
        uid_ortu = data.get("uid_orangtua", "")
        if requester_uid not in (uid_anak, uid_ortu):
            raise GuardianError("FORBIDDEN", "Anda bukan bagian dari pairing ini", 403)

        counts = {}
        for uid in (uid_anak, uid_ortu):
            u = users.document(uid).get(transaction=tx)
            counts[uid] = int((u.to_dict() or {}).get("pairing_count", 0)) if u.exists else 0

        tx.delete(ref)
        for uid, n in counts.items():
            tx.set(users.document(uid), {"pairing_count": max(0, n - 1)}, merge=True)

    _run_in_transaction(db, txn)
    logger.info("Pairing revoked: pairing_id=%s", pairing_id)


def _parent_wa(user_data: Dict[str, Any]) -> str:
    """Nomor WhatsApp darurat orang tua (``wa_darurat``), '' bila belum diisi."""
    return (user_data.get("wa_darurat") or "").strip()


def sync_parent_name(uid_orangtua: str) -> int:
    """Salin nama & nomor WA darurat orang tua dari users/{uid} ke semua pairing-nya.

    Anak tidak boleh membaca ``users`` milik orang tua, jadi penyalinan
    (``nama_ortu`` dan ``wa_ortu``, dipakai tombol SOS anak) dilakukan di sini
    (Admin SDK). Nomor yang dikosongkan orang tua ikut dikosongkan.
    Mengembalikan jumlah pairing yang diperbarui (0 bila tidak ada yang diubah).
    """
    from app.firebase_admin_client import get_firestore

    db = get_firestore()
    user_doc = db.collection("users").document(uid_orangtua).get()
    user_data = (user_doc.to_dict() or {}) if user_doc.exists else {}
    name = _display_name(user_data)

    fields: Dict[str, Any] = {"wa_ortu": _parent_wa(user_data)}
    if name:
        fields["nama_ortu"] = name

    batch = db.batch()
    count = 0
    for doc in db.collection("pairings").where("uid_orangtua", "==", uid_orangtua).stream():
        current = doc.to_dict() or {}
        # Dipanggil tiap app start: lewati pairing yang isinya sudah sama.
        if all(current.get(k) == v for k, v in fields.items()):
            continue
        batch.update(doc.reference, fields)
        count += 1
    if count:
        batch.commit()
        logger.info("Parent name synced to %d pairing(s)", count)
    return count


def sync_child_name(uid_anak: str) -> int:
    """Salin nama anak dari users/{uid} ke semua pairing-nya.

    Pasangan dari :func:`sync_parent_name`. Orang tua tidak boleh membaca
    ``users`` milik anak (lihat firestore.rules), jadi nama harus disalin ke
    dokumen pairing oleh Admin SDK supaya HP orang tua bisa menampilkannya.

    Hanya pairing yang namanya memang berbeda yang ditulis ulang, karena fungsi
    ini dipanggil tiap app start.

    Mengembalikan jumlah pairing yang benar-benar diperbarui.
    """
    from app.firebase_admin_client import get_firestore

    db = get_firestore()
    user_doc = db.collection("users").document(uid_anak).get()
    name = _display_name((user_doc.to_dict() or {}) if user_doc.exists else {})
    if not name:
        return 0

    batch = db.batch()
    count = 0
    for doc in db.collection("pairings").where("uid_anak", "==", uid_anak).stream():
        if (doc.to_dict() or {}).get("nama_anak") == name:
            continue
        batch.update(doc.reference, {"nama_anak": name})
        count += 1
    if count:
        batch.commit()
        logger.info("Child name synced to %d pairing(s)", count)
    return count


def get_active_pairing_for_child(uid_anak: str) -> Optional[Dict[str, Any]]:
    """Return the active pairing document for a child, or None."""
    from app.firebase_admin_client import get_firestore

    db = get_firestore()
    docs = (
        db.collection("pairings")
        .where("uid_anak", "==", uid_anak)
        .where("status", "==", "active")
        .limit(1)
        .stream()
    )
    for doc in docs:
        result = doc.to_dict()
        result["pairing_id"] = doc.id
        return result
    return None
