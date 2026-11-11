"""Profile sync endpoint."""

import logging

from flask import Blueprint, g, jsonify

from app.auth import require_auth
from app.errors import GuardianError

logger = logging.getLogger(__name__)

profile_bp = Blueprint("profile", __name__, url_prefix="/api/v1/profile")


@profile_bp.route("/sync-name", methods=["POST"])
@require_auth
def sync_name():
    """Propagate the caller's profile name to the pairings the other side reads.

    POST /api/v1/profile/sync-name

    Berlaku dua arah: orang tua menyalin ``nama_ortu`` (dipakai tombol SOS
    anak), anak menyalin ``nama_anak`` (dipakai daftar anak di HP orang tua).
    Aturan Firestore melarang masing-masing membaca users/{uid} pihak lain,
    jadi penyalinan harus lewat Admin SDK di sini.

    Nama dibaca dari users/{uid} (bukan dari body), jadi klien tidak bisa
    mengisi nama sembarang.
    """
    from app.services.pairing_service import sync_child_name, sync_parent_name

    if g.role == "parent":
        updated = sync_parent_name(g.uid)
    elif g.role == "child":
        updated = sync_child_name(g.uid)
    else:
        raise GuardianError("FORBIDDEN", "Peran tidak dikenali", 403)

    return jsonify(status="ok", updated=updated), 200
