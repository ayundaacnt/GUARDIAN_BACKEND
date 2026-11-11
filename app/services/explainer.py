"""LLM-powered explanation generator using Google Gemini.

When no API key is configured, falls back to a simple template so the
pipeline never breaks.
"""

import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Google menolak deadline di bawah 10 detik (400 INVALID_ARGUMENT).
_TIMEOUT_S = 10

_SYSTEM_PROMPT: str | None = None


def _load_system_prompt() -> str:
    """Load the system prompt from the bundled markdown file."""
    global _SYSTEM_PROMPT
    if _SYSTEM_PROMPT is not None:
        return _SYSTEM_PROMPT
    prompt_path = os.path.join(
        os.path.dirname(__file__), os.pardir, "prompts", "explain_system_prompt.md"
    )
    with open(prompt_path, "r", encoding="utf-8") as fh:
        _SYSTEM_PROMPT = fh.read()
    return _SYSTEM_PROMPT


def _template_fallback(text: str, label: str, confidence: float) -> Dict[str, Any]:
    """Return a static explanation when the LLM is not available."""
    return {
        "penjelasan": (
            "Pesan ini terdeteksi mengandung indikasi perundungan "
            "berdasarkan kata-kata yang digunakan. "
            "Disarankan untuk mendiskusikan hal ini dengan anak Anda."
        ),
        "kata_kunci": [],
        "sumber": "template",
    }


def explain(
    text: str,
    label: str,
    confidence: float,
    config,
) -> Dict[str, Any]:
    """Generate an explanation for a bullying prediction.

    Returns a dict with keys ``penjelasan`` (str) and ``kata_kunci`` (list[str]).
    """
    if not config.GEMINI_API_KEY:
        logger.info("No GEMINI_API_KEY configured; using template fallback")
        return _template_fallback(text, label, confidence)

    try:
        from google import genai

        client = genai.Client(api_key=config.GEMINI_API_KEY)

        system_prompt = _load_system_prompt()

        user_content = (
            f"Label: {label}\n"
            f"Confidence: {confidence:.2f}\n"
            f"---BEGIN MESSAGE---\n{text}\n---END MESSAGE---"
        )

        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=user_content,
            config=genai.types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.3,
                max_output_tokens=512,
                # Tanpa batas waktu, Gemini yang macet menahan alert & push.
                http_options=genai.types.HttpOptions(timeout=int(_TIMEOUT_S * 1000)),
            ),
        )

        raw = response.text.strip()
        # Try to parse JSON from the response.
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        result = json.loads(raw)
        return {
            "penjelasan": result.get("penjelasan", ""),
            "kata_kunci": result.get("kata_kunci", []),
            "sumber": "gemini",
        }

    except Exception:
        logger.exception("Gemini explanation call failed; using template fallback")
        return _template_fallback(text, label, confidence)
