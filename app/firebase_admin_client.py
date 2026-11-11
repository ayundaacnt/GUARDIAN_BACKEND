"""Firebase Admin SDK initialisation helpers."""

import logging
import os

import firebase_admin
from firebase_admin import credentials, firestore, auth

logger = logging.getLogger(__name__)

_app: firebase_admin.App | None = None


def _ensure_initialised() -> None:
    """Initialise the Firebase Admin SDK once."""
    global _app
    if _app is not None:
        return

    cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "")
    if cred_path and os.path.isfile(cred_path):
        cred = credentials.Certificate(cred_path)
        _app = firebase_admin.initialize_app(cred)
        logger.info("Firebase Admin initialised with service-account credentials")
    else:
        # Fall back to Application Default Credentials (ADC).
        _app = firebase_admin.initialize_app()
        logger.info("Firebase Admin initialised with ADC (no credential file)")


def get_firestore() -> firestore.firestore.Client:
    """Return a Firestore client, initialising Firebase if needed."""
    _ensure_initialised()
    return firestore.client()


def get_auth() -> auth:
    """Return the firebase_admin.auth module, initialising Firebase if needed."""
    _ensure_initialised()
    return auth
