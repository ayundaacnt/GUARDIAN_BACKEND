"""Flask application factory for the Guardian backend."""

import logging
import os

from flask import Flask, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from app.config import Config
from app.errors import register_error_handlers


limiter = Limiter(
    key_func=get_remote_address,
    storage_uri="memory://",
)


def create_app(config: Config | None = None) -> Flask:
    """Create and configure the Flask application.

    Args:
        config: Optional Config instance. If None, a new one is created
                from environment variables.

    Returns:
        Configured Flask application.
    """
    app = Flask(__name__)

    if config is None:
        config = Config()

    app.config["GUARDIAN"] = config

    # --------------- logging ---------------
    log_level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger(__name__)
    logger.info("Starting Guardian backend")

    # --------------- rate limiter ---------------
    limiter.init_app(app)

    # --------------- blueprints ---------------
    from app.routes.health import health_bp
    from app.routes.classify import classify_bp
    from app.routes.pairing import pairing_bp
    from app.routes.devices import devices_bp
    from app.routes.profile import profile_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(classify_bp)
    app.register_blueprint(devices_bp)
    app.register_blueprint(pairing_bp)

    # Endpoint diagnostik: hanya aktif kalau sengaja dinyalakan.
    if config.DEBUG_INFERENCE:
        from app.routes.cek import cek_bp

        app.register_blueprint(cek_bp)
        logger.warning("DEBUG_INFERENCE aktif: POST /api/v1/cek terdaftar")

    # --------------- model preload ---------------
    if config.PRELOAD_MODEL:
        import threading

        def _preload() -> None:
            try:
                from app.services.classifier import get_classifier

                # Satu prediksi pemanasan: tanpa ini prediksi pertama ~2,6 detik.
                get_classifier(config).predict("tes")
                logger.info("Classifier model preloaded and warmed up")
            except Exception:
                logger.exception("Preload model gagal; akan dicoba lagi saat request pertama")

        threading.Thread(target=_preload, name="model-preload", daemon=True).start()

    # --------------- error handlers ---------------
    register_error_handlers(app)

    # --------------- fallback 404 / 405 ---------------
    @app.errorhandler(404)
    def not_found(_e):
        return jsonify(error={"code": "NOT_FOUND", "message": "Resource not found"}), 404

    @app.errorhandler(405)
    def method_not_allowed(_e):
        return jsonify(error={"code": "METHOD_NOT_ALLOWED", "message": "Method not allowed"}), 405

    return app
