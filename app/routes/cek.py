"""Endpoint diagnostik: probabilitas bully mentah untuk satu bubble.

Dipakai untuk kalibrasi ambang dan pengujian manual, BUKAN jalur produksi.
Jalur produksi tetap ``POST /api/v1/classify``, yang mengerjakan pairing,
dedup, penjelasan, penyimpanan, dan push.

Endpoint ini mati kecuali ``DEBUG_INFERENCE=true``, dan tetap menuntut
autentikasi Firebase serta rate limit: tanpa itu ia jadi mesin inferensi
terbuka bagi siapa pun yang tahu URL-nya.

Teks yang dikirim ke sini tidak pernah dicatat maupun disimpan, sama seperti
pesan non-bully di jalur utama.
"""

import logging

from flask import Blueprint, current_app, jsonify, request
from pydantic import BaseModel, Field

from app import limiter
from app.auth import require_auth
from app.errors import GuardianError

logger = logging.getLogger(__name__)

cek_bp = Blueprint("cek", __name__, url_prefix="/api/v1")

_MAX_TEXT_LENGTH = 2000


class CekRequest(BaseModel):
    teks: str = Field(..., min_length=1, max_length=_MAX_TEXT_LENGTH)


@cek_bp.route("/cek", methods=["POST"])
@require_auth
@limiter.limit(lambda: current_app.config["GUARDIAN"].RATE_LIMIT_CLASSIFY)
def cek():
    """Return raw bully probability for one chat bubble.

    POST /api/v1/cek  {"teks": "..."}  ->  {"p_bully": 0.93, "bully": true}
    """
    config = current_app.config["GUARDIAN"]

    body = request.get_json(silent=True)
    if not body:
        raise GuardianError("INVALID_INPUT", "Request body must be JSON", 400)

    try:
        payload = CekRequest(**body)
    except Exception as exc:
        raise GuardianError("INVALID_INPUT", str(exc), 400)

    from app.services.classifier import get_classifier

    prediction = get_classifier(config).predict(payload.teks)
    p_bully = float(prediction["prob_bully"])
    ambang = config.BULLY_THRESHOLD

    # Panjang dan hasil saja; isi pesan tidak dicatat.
    logger.info("cek: p_bully=%.3f ambang=%.2f len=%d", p_bully, ambang, len(payload.teks))

    return jsonify(
        p_bully=round(p_bully, 4),
        bully=p_bully >= ambang,
        ambang=ambang,
    ), 200
