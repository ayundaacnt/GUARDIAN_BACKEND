"""Uniform error handling for the Guardian API."""

import logging

from flask import Flask, jsonify

logger = logging.getLogger(__name__)


class GuardianError(Exception):
    """Base exception that produces a uniform JSON error response.

    Response format::

        {
            "error": {
                "code": "SOME_CODE",
                "message": "Human-readable description"
            }
        }
    """

    def __init__(self, code: str, message: str, http_status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def register_error_handlers(app: Flask) -> None:
    """Register custom error handlers on the Flask app."""

    @app.errorhandler(GuardianError)
    def handle_guardian_error(exc: GuardianError):
        return (
            jsonify(error={"code": exc.code, "message": exc.message}),
            exc.http_status,
        )

    @app.errorhandler(429)
    def handle_rate_limit(_exc):
        return (
            jsonify(error={"code": "RATE_LIMITED", "message": "Too many requests"}),
            429,
        )

    @app.errorhandler(500)
    def handle_internal(_exc):
        logger.exception("Unhandled server error")
        return (
            jsonify(error={"code": "INTERNAL", "message": "Internal server error"}),
            500,
        )
