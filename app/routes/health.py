"""Health-check endpoint."""

from flask import Blueprint, jsonify

health_bp = Blueprint("health", __name__)

_VERSION = "0.1.0"
_CLASSIFIER = "indobertweet-bilstm"


@health_bp.route("/health", methods=["GET"])
def health():
    """Return service health status.

    GET /health
    """
    return jsonify(status="ok", version=_VERSION, classifier=_CLASSIFIER)
