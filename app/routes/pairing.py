"""Pairing management endpoints.

Satu-satunya jalur penulisan pairing: klien tidak boleh menulis ke koleksi
``pairing_codes`` / ``pairings`` (firestore.rules). Endpoint yang menerima
kode dari pengguna (validate, redeem) dibatasi per akun dan per IP untuk
menghambat tebak-tebakan kode 6 digit.
"""

import logging

from flask import Blueprint, current_app, g, jsonify, request
from flask_limiter.util import get_remote_address

from app import limiter
from app.auth import require_auth
from app.errors import GuardianError

logger = logging.getLogger(__name__)

pairing_bp = Blueprint("pairing", __name__, url_prefix="/api/v1/pairing")


def _uid_key() -> str:
    return getattr(g, "uid", None) or get_remote_address()


def _limit_uid() -> str:
    return current_app.config["GUARDIAN"].RATE_LIMIT_PAIRING_UID


def _limit_ip() -> str:
    return current_app.config["GUARDIAN"].RATE_LIMIT_PAIRING_IP


def _require_role(role: str, action: str) -> None:
    if g.role != role:
        who = "child" if role == "child" else "parent"
        raise GuardianError("FORBIDDEN", f"Only {who} accounts may {action}", 403)


def _code_from_body() -> str:
    body = request.get_json(silent=True) or {}
    code = body.get("code") or body.get("pairing_code") or ""
    code = code.strip() if isinstance(code, str) else ""
    if not code:
        raise GuardianError("INVALID_INPUT", "code is required", 400)
    return code


# --------------- POST /code  (child) ---------------

@pairing_bp.route("/code", methods=["POST"])
@require_auth
@limiter.limit(_limit_uid, key_func=_uid_key)
def create_code():
    """Buat kode pairing baru. Hanya role child."""
    _require_role("child", "generate a pairing code")

    from app.services.pairing_service import create_pairing_code

    data = create_pairing_code(uid_anak=g.uid)
    return jsonify(
        code=data["code"],
        created_at=data["created_at"],
        expires_at=data["expires_at"],
    ), 201


# --------------- POST /cancel  (child) ---------------

@pairing_bp.route("/cancel", methods=["POST"])
@require_auth
def cancel():
    """Batalkan kode yang masih berlaku. Hanya role child."""
    _require_role("child", "cancel a pairing code")

    from app.services.pairing_service import cancel_code

    cancel_code(uid_anak=g.uid)
    return jsonify(status="cancelled"), 200


# --------------- POST /validate  (parent) ---------------

@pairing_bp.route("/validate", methods=["POST"])
@require_auth
@limiter.limit(_limit_uid, key_func=_uid_key)
@limiter.limit(_limit_ip, key_func=get_remote_address)
def validate():
    """Pratinjau kode (nama/foto anak) tanpa menebusnya. Hanya role parent."""
    _require_role("parent", "validate a pairing code")

    from app.services.pairing_service import validate_code

    return jsonify(validate_code(pairing_code=_code_from_body())), 200


# --------------- POST /redeem  (parent) ---------------

@pairing_bp.route("/redeem", methods=["POST"])
@require_auth
@limiter.limit(_limit_uid, key_func=_uid_key)
@limiter.limit(_limit_ip, key_func=get_remote_address)
def redeem():
    """Tebus kode dan buat pairing. Hanya role parent."""
    _require_role("parent", "redeem a pairing code")

    from app.services.pairing_service import redeem_code

    pairing = redeem_code(pairing_code=_code_from_body(), uid_orangtua=g.uid)
    return jsonify(pairing_id=pairing["id"], pairing=pairing), 200


# --------------- POST /revoke  (parent or child) ---------------

@pairing_bp.route("/revoke", methods=["POST"])
@require_auth
def revoke():
    """Putus pairing. Boleh dipanggil anak atau orang tua dalam pairing itu."""
    body = request.get_json(silent=True) or {}
    pairing_id = body.get("pairing_id", "")
    pairing_id = pairing_id.strip() if isinstance(pairing_id, str) else ""
    if not pairing_id:
        raise GuardianError("INVALID_INPUT", "pairing_id is required", 400)

    from app.services.pairing_service import revoke_pairing

    revoke_pairing(pairing_id=pairing_id, requester_uid=g.uid)
    return jsonify(status="revoked"), 200
