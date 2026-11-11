"""Authentication decorator using Firebase ID tokens."""

import functools
import logging

from flask import g, request, jsonify

from app.firebase_admin_client import get_firestore, get_auth

logger = logging.getLogger(__name__)


def require_auth(fn):
    """Decorator that verifies the Firebase ID token in the Authorization header.

    On success the following are attached to ``flask.g``:
    - ``g.uid``  -- Firebase UID
    - ``g.role`` -- role string from Firestore users/{uid} document

    On failure an error JSON response is returned directly.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return (
                jsonify(error={"code": "UNAUTHENTICATED", "message": "Missing or malformed Authorization header"}),
                401,
            )

        id_token = auth_header.split("Bearer ", 1)[1]

        try:
            decoded = get_auth().verify_id_token(id_token)
        except Exception as exc:
            # Alasan dicatat (mis. selisih jam, project berbeda); token tidak dicatat.
            logger.warning("Invalid ID token presented: %s: %s", type(exc).__name__, exc)
            return (
                jsonify(error={"code": "UNAUTHENTICATED", "message": "Invalid or expired token"}),
                401,
            )

        uid = decoded["uid"]
        g.uid = uid

        # Load role from Firestore users collection.
        try:
            user_doc = get_firestore().collection("users").document(uid).get()
            if user_doc.exists:
                g.role = user_doc.to_dict().get("role", "unknown")
            else:
                g.role = "unknown"
        except Exception:
            logger.exception("Failed to load user document for uid")
            g.role = "unknown"

        return fn(*args, **kwargs)

    return wrapper
